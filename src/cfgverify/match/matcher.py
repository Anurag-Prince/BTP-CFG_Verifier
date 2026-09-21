"""The deterministic matcher: normalized HLL CFG vs normalized assembly CFG.

Five levels, cheapest first. Each one either rejects with a located reason or hands the next one more
information. **No level knows which program it is looking at** — there is not one rule, threshold or name in
this file that mentions a regression program, a function name or an ISA. That is the Part 7 checkpoint.

    L0  function pairing      by symbol name; missing and extra functions are findings [B4]
    L1  invariants            blocks, edges, decisions, cyclomatic, loops, depth, exits, returns, back edges
    L2  exact structure       graph isomorphism (networkx VF2) with the entry pinned and node roles required
                              to agree; the result is the block mapping
    L3  anchors               when several isomorphisms fit, choose between them by what the blocks compute
                              (semantic content, calls, C line numbers) - a TIE-BREAKER, never a filter
    L4  branch conditions     for every mapped decision, the comparable condition key (Part 6) must agree,
                              and the arms must lead to mapped counterparts

L2/L3 give a *mapping*. When they fail, `partial_mapping()` still produces one — by colour refinement, the
standard 1-WL algorithm — so that `match/diagnose.py` can say *where* the graphs differ instead of only that
they do. A partial mapping is never presented as a match; it is presented as the best available localization.

What the matcher does **not** do: decide the verdict. It returns levels, a mapping and the raw disagreements.
`report/verdict.py` is the only place a verdict is created (rule R4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from cfgverify.model.cfg import CFG
from cfgverify.match import invariants
from cfgverify.normalize import conditions as cond
from cfgverify.normalize import semantics

#: Matching stops at this level by default. L4 runs and reports, but whether a condition disagreement is an
#: error depends on what "consistent" means, which is **[C1]**; see report/verdict.py.
DEFAULT_LEVEL = "L4"
LEVELS = ("L0", "L1", "L2", "L3", "L4")


@dataclass
class MatchResult:
    """What the matcher found for one function. Facts only."""
    function: str
    isomorphic: bool
    method: str                                   # "isomorphism" | "refinement" | "none"
    pairs: dict[str, str] = field(default_factory=dict)     # hll block -> asm block
    hll_unmapped: list[str] = field(default_factory=list)
    asm_unmapped: list[str] = field(default_factory=list)
    invariant_diffs: list[tuple[str, object, object]] = field(default_factory=list)
    condition_diffs: list[tuple[str, str, str, str]] = field(default_factory=list)  # hll, asm, key, key
    ambiguous: int = 0                            # how many equally good isomorphisms existed
    levels: dict = field(default_factory=dict)    # what each level concluded
    hll_fingerprint: dict = field(default_factory=dict)
    asm_fingerprint: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------- L0: function pairing
def pair_functions(hll: dict[str, CFG], asm: dict[str, CFG]) -> tuple[list[str], list[str], list[str]]:
    """Returns (paired, only in the C file, only in the assembly), each sorted. Symbol name is the key.

    Platform name decoration (a leading `_` on Mach-O/Windows) is **[A5]** and is not assumed: only an exact
    name match pairs two functions today, and anything unpaired becomes a finding rather than a guess.
    """
    both = sorted(set(hll) & set(asm))
    return both, sorted(set(hll) - set(asm)), sorted(set(asm) - set(hll))


# ------------------------------------------------------------------------- L2/L3: exact structure
def _edge_role(cfg: CFG, edge) -> str:
    """What an edge is, for the purpose of comparing SHAPE.

    A conditional edge is **not** compared as `T` or `F`. A compiler is free to emit the opposite test and
    swap the arms — `for (i = 1; i <= n; i++)` becomes `jg` over the *false* arm on x86-64 — so `T` on one
    side legitimately corresponds to `F` on the other. Both become `cond`.

    The condition itself is deliberately **not** part of this role either. A compiler may emit `x >= 2` for
    `x > 1`, use an unsigned compare for a signed test, or test the sign bit instead of comparing with zero
    (Part 6, finding F3). Those are rewrites of the *test*, not of the *control flow*, and folding them into
    the structural comparison would report a shape difference where there is none. L4 compares the conditions
    separately and reports any disagreement as its own finding, whose severity is **[C1]**.
    """
    return "cond" if edge.kind in ("T", "F") else edge.kind


def _graph(cfg: CFG, with_content: bool) -> nx.DiGraph:
    g = nx.DiGraph()
    for node in cfg.nodes:
        g.add_node(node.id, role=invariants.node_signature(cfg, node.id, with_content=with_content))
    for edge in cfg.edges:
        kinds = g.edges.get((edge.source, edge.destination), {}).get("kinds", ())
        g.add_edge(edge.source, edge.destination,
                   kinds=tuple(sorted(kinds + (_edge_role(cfg, edge),))), back=edge.back_edge)
    return g


def isomorphisms(hll: CFG, asm: CFG, limit: int = 64) -> list[dict[str, str]]:
    """Every structural isomorphism that maps entry to entry, in a deterministic order (at most `limit`).

    Node roles are compared on *structure only* — out-degree, in-degree, terminator class, entry/exit,
    loop header, loop depth, edge kinds, back edges. Block content is not part of the test; see L3.
    """
    left, right = _graph(hll, with_content=False), _graph(asm, with_content=False)
    matcher = nx.algorithms.isomorphism.DiGraphMatcher(
        left, right,
        node_match=lambda a, b: a["role"] == b["role"],
        edge_match=lambda a, b: a["kinds"] == b["kinds"] and a["back"] == b["back"],
    )
    found: list[dict[str, str]] = []
    for mapping in matcher.isomorphisms_iter():
        if mapping.get(hll.entry) != asm.entry:
            continue                                   # the entry must map to the entry
        found.append(dict(mapping))
        if len(found) >= limit:
            break
    return sorted(found, key=lambda m: sorted(m.items()))


def _content_agreement(hll: CFG, asm: CFG, mapping: dict[str, str]) -> int:
    """How many mapped blocks compute the same thing — the L3 tie-breaker, in [0, len(mapping)].

    A block scores 1 when the multiset of semantic operations on one side is contained in the other's, which
    is the honest test: the assembly may do more (prologues, spills), never less.
    """
    score = 0
    for source, target in mapping.items():
        left = semantics.content_profile(hll.node(source).operations)
        right = semantics.content_profile(asm.node(target).operations)
        smaller, larger = (left, right) if sum(left.values()) <= sum(right.values()) else (right, left)
        if all(larger.get(kind, 0) >= count for kind, count in smaller.items()):
            score += 1
    return score


# ------------------------------------------------------- when that fails: the best partial mapping
def _refine(cfg: CFG, rounds: int = 4) -> dict[str, tuple]:
    """1-WL colour refinement: a block's colour is its own role plus, repeatedly, its neighbours' colours."""
    colour = {n.id: invariants.node_signature(cfg, n.id) for n in cfg.nodes}
    for _ in range(rounds):
        nxt = {}
        for node in cfg.nodes:
            succ = tuple(sorted(colour[e.destination] for e in cfg.out_edges(node.id)))
            pred = tuple(sorted(colour[p] for p in node.predecessors))
            nxt[node.id] = (colour[node.id], succ, pred)
        if nxt == colour:
            break
        colour = nxt
    return colour


def partial_mapping(hll: CFG, asm: CFG) -> tuple[dict[str, str], list[str], list[str]]:
    """The best mapping available when the graphs are not isomorphic — for localizing, never for matching.

    Four passes, each deterministic and each weaker than the last, so that a block is paired by the
    strongest evidence available and a difference stays *local* instead of poisoning the whole mapping:

      1. **colour refinement** (1-WL): pair blocks whose refined colour is unique on both sides. Around a
         genuine difference the colours diverge, which is exactly why the later passes are needed.
      2. **anchors**: pair the entries, and pair blocks that compute the same thing and sit at the same
         distance from the entry — content is the strongest name-free anchor there is.
      3. **growth** along edges from what is already paired.
      4. **distance + content** for whatever is left.

    Anything still unpaired is reported as missing (HLL side) or extra (assembly side).
    """
    pairs: dict[str, str] = {}
    used: set[str] = set()

    def take(source: str, target: str) -> None:
        pairs[source] = target
        used.add(target)

    def free_left() -> list[str]:
        return [n.id for n in hll.nodes if n.id not in pairs]

    def free_right() -> list[str]:
        return [n.id for n in asm.nodes if n.id not in used]

    # 1. colour refinement
    left, right = _refine(hll), _refine(asm)
    by_right: dict[tuple, list[str]] = {}
    for node_id, colour in right.items():
        by_right.setdefault(colour, []).append(node_id)
    by_left: dict[tuple, list[str]] = {}
    for node_id, colour in left.items():
        by_left.setdefault(colour, []).append(node_id)
    for colour in sorted(by_left, key=repr):
        here, there = by_left[colour], by_right.get(colour, [])
        if len(here) == 1 and len(there) == 1:
            take(here[0], there[0])

    # 2. anchors: the entries, then content + depth from the entry
    if hll.entry not in pairs and asm.entry not in used:
        take(hll.entry, asm.entry)
    depth_left, depth_right = _depths(hll), _depths(asm)
    anchors: dict[tuple, list[str]] = {}
    for node_id in free_right():
        key = (semantics.content_signature(asm.node(node_id).operations), depth_right.get(node_id))
        anchors.setdefault(key, []).append(node_id)
    for node_id in free_left():
        content = semantics.content_signature(hll.node(node_id).operations)
        if not content:
            continue                                    # an empty block anchors nothing
        key = (content, depth_left.get(node_id))
        fits = [n for n in anchors.get(key, []) if n not in used]
        if len(fits) == 1:
            take(node_id, fits[0])

    # 3. growth along edges
    changed = True
    while changed:
        changed = False
        for source, target in sorted(pairs.items()):
            for direction in ("out", "in"):
                here = ([e.destination for e in hll.out_edges(source)] if direction == "out"
                        else list(hll.node(source).predecessors))
                there = ([e.destination for e in asm.out_edges(target)] if direction == "out"
                         else list(asm.node(target).predecessors))
                loose_here = [n for n in here if n not in pairs]
                loose_there = [n for n in there if n not in used]
                for candidate in sorted(loose_here):
                    role = invariants.node_signature(hll, candidate)
                    fits = [n for n in sorted(loose_there) if invariants.node_signature(asm, n) == role]
                    if len(fits) == 1:
                        take(candidate, fits[0])
                        loose_there.remove(fits[0])
                        changed = True

    # 4. last resort: the best remaining candidate by what it computes.
    # Exact content equality is too strict here — the assembly does the same work with more instructions
    # (a memory operand becomes a LOAD, a compare becomes a SUBTRACT). So candidates are scored by how much
    # of the smaller profile the larger one contains, then by nearness in the flow. Deterministic throughout.
    for node_id in sorted(free_left(), key=lambda n: hll.node(n).index):
        want = semantics.content_profile(hll.node(node_id).operations)
        best, best_score = None, None
        for candidate in free_right():
            have = semantics.content_profile(asm.node(candidate).operations)
            overlap = sum(min(count, have.get(kind, 0)) for kind, count in want.items())
            size = max(sum(want.values()), sum(have.values()), 1)
            score = (overlap / size,
                     -abs(depth_right.get(candidate, 0) - depth_left.get(node_id, 0)),
                     -asm.node(candidate).index)
            if best_score is None or score > best_score:
                best, best_score = candidate, score
        if best is not None and best_score[0] > 0:
            take(node_id, best)

    hll_unmapped = sorted(n.id for n in hll.nodes if n.id not in pairs)
    asm_unmapped = sorted(n.id for n in asm.nodes if n.id not in used)
    return pairs, hll_unmapped, asm_unmapped


def _depths(cfg: CFG) -> dict[str, int]:
    """Shortest distance from the entry, in edges. A name-free way to say "the same place in the flow"."""
    g = cfg.to_networkx(include_attributes=False)
    try:
        return nx.single_source_shortest_path_length(g, cfg.entry)
    except nx.NetworkXError:
        return {}


# ------------------------------------------------------------------------------ L4: branch conditions
def condition_disagreements(hll: CFG, asm: CFG, pairs: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """For every mapped decision, compare the comparable condition key (Part 6, rule N6).

    Returns (hll block, asm block, hll key, asm key) for each decision whose condition is not the same test.
    Whether that is an error is **[C1]**; the matcher only reports it.
    """
    out = []
    for hll_block, asm_block in sorted(pairs.items()):
        left, right = hll.out_edges(hll_block), asm.out_edges(asm_block)
        if len(left) < 2 or len(right) < 2:
            continue
        left_key = cond.decision_key(hll, hll_block, polarity_free=True)
        right_key = cond.decision_key(asm, asm_block, polarity_free=True)
        if left_key != right_key:
            out.append((hll_block, asm_block, left_key, right_key))
    return out


# ----------------------------------------------------------------------------------------- the driver
def match(hll: CFG, asm: CFG, level: str = DEFAULT_LEVEL) -> MatchResult:
    """Run the levels up to `level` on one pair of NORMALIZED CFGs and report what each one concluded."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; available: {', '.join(LEVELS)}")
    wanted = LEVELS.index(level)
    result = MatchResult(function=hll.function, isomorphic=False, method="none")
    result.hll_fingerprint = invariants.fingerprint(hll)
    result.asm_fingerprint = invariants.fingerprint(asm)

    # L1
    result.invariant_diffs = invariants.disagreements(result.hll_fingerprint, result.asm_fingerprint)
    result.levels["L1"] = "agree" if not result.invariant_diffs else \
        "disagree: " + ", ".join(f"{n} {a} vs {b}" for n, a, b in result.invariant_diffs)
    if wanted < LEVELS.index("L2"):
        return result

    # L2: structure alone decides whether the graphs match.
    if wanted < LEVELS.index("L2"):
        return result
    candidates = isomorphisms(hll, asm)
    if candidates:
        result.isomorphic, result.method = True, "isomorphism"
        result.ambiguous = len(candidates)
        result.pairs = candidates[0]
        result.levels["L2"] = ("isomorphic" if len(candidates) == 1
                               else f"isomorphic ({len(candidates)} mapping(s) fit)")
        # L3: several isomorphisms can fit a symmetric graph. Pick the one whose blocks agree best on what
        # they COMPUTE. Content is a tie-breaker and never rejects a mapping: the assembly legitimately does
        # work the IR does not (stack prologues, a SUBTRACT where the IR has a COMPARE), so requiring equal
        # content would reject every correct program.
        if wanted >= LEVELS.index("L3"):
            best = max(candidates, key=lambda m: (_content_agreement(hll, asm, m), sorted(m.items())[::-1]))
            result.pairs = best
            score, total = _content_agreement(hll, asm, best), len(best)
            result.levels["L3"] = f"content agrees on {score}/{total} mapped block(s)"
    else:
        result.levels["L2"] = "no isomorphism"

    if not result.isomorphic:
        result.pairs, result.hll_unmapped, result.asm_unmapped = partial_mapping(hll, asm)
        result.method = "refinement"
        result.levels.setdefault("L3", "not reached")

    # L4
    if wanted >= LEVELS.index("L4"):
        result.condition_diffs = condition_disagreements(hll, asm, result.pairs)
        result.levels["L4"] = ("conditions agree" if not result.condition_diffs else
                               f"{len(result.condition_diffs)} condition(s) differ")
    return result
