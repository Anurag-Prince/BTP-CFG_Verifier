"""Turn what the matcher found into **located findings**.

Saying "MISMATCH" is not useful. Saying *"the back edge of the loop whose header came from `while.cond`
(C line 11) has no counterpart in the assembly, whose block `.LBB0_1` at line 24 does not branch back"* is.
Every `Finding` this module produces names the blocks on both sides, the raw labels they came from, the
assembly lines and — when `-g` gave them — the C lines.

The ten kinds the brief asks for, and what produces each one:

| Kind | Produced when |
|---|---|
| `MISSING_BLOCK` | a normalized HLL block has no counterpart in the mapping |
| `EXTRA_BLOCK` | a normalized assembly block has no counterpart |
| `MISSING_EDGE` | both endpoints are mapped, the HLL has the edge, the assembly does not |
| `EXTRA_EDGE` | both endpoints are mapped, the assembly has the edge, the HLL does not |
| `WRONG_BRANCH` | a mapped block branches a different number of ways, or an arm leads somewhere else |
| `WRONG_BRANCH_CONDITION` | the arms line up but the comparable condition key differs (L4) |
| `MISSING_LOOP_BACK_EDGE` | an HLL back edge has no back edge between the mapped counterparts |
| `UNEXPECTED_PATH` | the assembly can perform a sequence of semantic operations the HLL cannot |
| `MISSING_RETURN` | the HLL has more return paths than the assembly |
| `STRUCTURAL_MISMATCH` | an L1 invariant disagrees and none of the above explains it |

Ordering is deterministic: kinds in the order above, then by block index. Nothing here decides a verdict.
"""

from __future__ import annotations

import networkx as nx

from cfgverify.model.cfg import CFG
from cfgverify.model.evidence import Finding, new_finding
from cfgverify.match.matcher import MatchResult
from cfgverify.normalize import semantics
from cfgverify.normalize.rules import EXIT_ID, TRAP_ID

VIRTUAL = (EXIT_ID, TRAP_ID)
_ORDER = ("MISSING_BLOCK", "EXTRA_BLOCK", "MISSING_EDGE", "EXTRA_EDGE", "WRONG_BRANCH",
          "WRONG_BRANCH_CONDITION", "MISSING_LOOP_BACK_EDGE", "UNEXPECTED_PATH", "MISSING_RETURN",
          "STRUCTURAL_MISMATCH", "UNSUPPORTED_CONSTRUCT", "AMBIGUOUS_MAPPING")
#: The L1 invariants that a block/edge/loop/return finding already explains, so they are not repeated.
_EXPLAINED_BY = {
    "blocks": ("MISSING_BLOCK", "EXTRA_BLOCK"),
    "edges": ("MISSING_EDGE", "EXTRA_EDGE", "MISSING_LOOP_BACK_EDGE"),
    "cyclomatic": ("MISSING_EDGE", "EXTRA_EDGE", "MISSING_BLOCK", "EXTRA_BLOCK"),
    "decisions": ("WRONG_BRANCH",),
    "loops": ("MISSING_LOOP_BACK_EDGE",),
    "back_edges": ("MISSING_LOOP_BACK_EDGE",),
    "max_loop_depth": ("MISSING_LOOP_BACK_EDGE",),
    "exits": ("MISSING_RETURN",),
    "returns": ("MISSING_RETURN",),
}


def _labels(cfg: CFG, *block_ids: str) -> tuple[str, ...]:
    """The raw block labels behind a normalized block — what a reader can find in the .ll or .s file."""
    out: list[str] = []
    for block_id in block_ids:
        try:
            node = cfg.node(block_id)
        except KeyError:
            continue
        out += list(node.origin_blocks or [node.label])
    return tuple(out)


def _lines(cfg: CFG, *block_ids: str) -> tuple[int, ...]:
    out: set[int] = set()
    for block_id in block_ids:
        try:
            node = cfg.node(block_id)
        except KeyError:
            continue
        out |= {i.line for i in node.instructions if i.line}
    return tuple(sorted(out))


def _c_lines(cfg: CFG, *block_ids: str) -> tuple[int, ...]:
    out: set[int] = set()
    for block_id in block_ids:
        try:
            out |= set(cfg.node(block_id).source_lines)
        except KeyError:
            continue
    return tuple(sorted(out))


def _path_behaviours(cfg: CFG, limit: int = 20000) -> set[tuple[str, ...]] | None:
    """What the function can do, as sequences of semantic operations — the Part 6 acyclic view, reused."""
    g = nx.DiGraph()
    g.add_nodes_from(n.id for n in cfg.nodes)
    g.add_node("#LOOP")
    for edge in cfg.edges:
        g.add_edge(edge.source, "#LOOP" if edge.back_edge else edge.destination)
    content = {n.id: () if n.id in VIRTUAL else semantics.content_signature(n.operations) for n in cfg.nodes}
    content["#LOOP"] = ()
    out: set[tuple[str, ...]] = set()
    for target in [n for n in g.nodes if g.out_degree(n) == 0]:
        for path in nx.all_simple_paths(g, cfg.entry, target):
            out.add(tuple(op for node in path for op in content[node]))
            if len(out) > limit:
                return None
    return out


def diagnose(hll: CFG, asm: CFG, result: MatchResult, counter: list[int] | None = None) -> list[Finding]:
    """Every located difference between two NORMALIZED CFGs, given what the matcher concluded."""
    counter = counter if counter is not None else [0]
    function = hll.function
    findings: list[Finding] = []

    def add(kind: str, severity: str, description: str, hll_blocks=(), asm_blocks=(), **detail):
        findings.append(new_finding(
            counter, kind, severity, function, description,
            hll_blocks=tuple(hll_blocks), asm_blocks=tuple(asm_blocks),
            hll_labels=_labels(hll, *hll_blocks), asm_labels=_labels(asm, *asm_blocks),
            asm_lines=_lines(asm, *asm_blocks), c_lines=_c_lines(hll, *hll_blocks) or _c_lines(asm, *asm_blocks),
            detail=detail))

    # Unsupported constructs are reported whatever the shape turned out to be. A block that falls off the
    # end of a function, an indirect jump or a jump table means the CFG does not describe the whole program,
    # so no confident verdict is possible even if the graphs happen to be isomorphic [C3].
    _unsupported(hll, asm, add)

    if result.isomorphic:
        # The graphs are the same shape. L4 and the non-structural invariants can still have something to say.
        _conditions(hll, asm, result, add)
        _unexplained_invariants(result, findings, add)
        if result.ambiguous > 1:
            add("AMBIGUOUS_MAPPING", "info",
                f"{result.ambiguous} block mappings fit equally well; the first in sorted order was used",
                count=result.ambiguous)
        return _sorted(findings)

    pairs = result.pairs
    reverse = {v: k for k, v in pairs.items()}

    # ---- blocks
    for block in result.hll_unmapped:
        if block in VIRTUAL:
            continue
        node = hll.node(block)
        add("MISSING_BLOCK", "error",
            f"HLL block {block} ({'+'.join(_labels(hll, block))}) has no counterpart in the assembly; "
            f"it performs {' '.join(semantics.content_signature(node.operations)) or 'no computation'}",
            hll_blocks=[block], content=list(semantics.content_signature(node.operations)))
    for block in result.asm_unmapped:
        if block in VIRTUAL:
            continue
        node = asm.node(block)
        add("EXTRA_BLOCK", "error",
            f"assembly block {block} ({'+'.join(_labels(asm, block))}) has no counterpart in the C program; "
            f"it performs {' '.join(semantics.content_signature(node.operations)) or 'no computation'}",
            asm_blocks=[block], content=list(semantics.content_signature(node.operations)))

    # ---- edges and branches between mapped blocks
    for hll_block in sorted(pairs, key=lambda b: _index(hll, b)):
        asm_block = pairs[hll_block]
        left = hll.out_edges(hll_block)
        right = asm.out_edges(asm_block)
        left_targets = {e.destination: e for e in left}
        right_targets = {e.destination: e for e in right}

        for destination, edge in sorted(left_targets.items(), key=lambda kv: _index(hll, kv[0])):
            mapped = pairs.get(destination)
            if mapped is None:
                continue                                     # the MISSING_BLOCK finding already says it
            if mapped not in right_targets:
                kind = "MISSING_LOOP_BACK_EDGE" if edge.back_edge else "MISSING_EDGE"
                what = "loop back edge" if edge.back_edge else f"{edge.kind} edge"
                add(kind, "error",
                    f"the {what} {hll_block} -> {destination} has no counterpart: assembly block {asm_block} "
                    f"does not go to {mapped}",
                    hll_blocks=[hll_block, destination], asm_blocks=[asm_block, mapped],
                    edge_kind=edge.kind, back_edge=edge.back_edge)
        for destination, edge in sorted(right_targets.items(), key=lambda kv: _index(asm, kv[0])):
            origin = reverse.get(destination)
            if origin is None:
                continue                                     # the EXTRA_BLOCK finding already says it
            if origin not in left_targets:
                add("EXTRA_EDGE", "error",
                    f"the assembly has a {edge.kind} edge {asm_block} -> {destination} with no counterpart: "
                    f"HLL block {hll_block} does not go to {origin}",
                    hll_blocks=[hll_block, origin], asm_blocks=[asm_block, destination],
                    edge_kind=edge.kind, back_edge=edge.back_edge)

        if len(left) != len(right):
            add("WRONG_BRANCH", "error",
                f"HLL block {hll_block} branches {len(left)} way(s) and the assembly block {asm_block} "
                f"branches {len(right)} way(s)",
                hll_blocks=[hll_block], asm_blocks=[asm_block],
                hll_arms=len(left), asm_arms=len(right))
        elif len(left) >= 2:
            arms_ok = all(pairs.get(l.destination) == r.destination and l.kind == r.kind
                          for l, r in zip(left, right))
            if not arms_ok and all(pairs.get(e.destination) for e in left):
                add("WRONG_BRANCH", "error",
                    f"the arms of the decision in {hll_block} lead elsewhere in the assembly block "
                    f"{asm_block}: {[ (e.kind, e.destination) for e in left ]} vs "
                    f"{[ (e.kind, e.destination) for e in right ]}",
                    hll_blocks=[hll_block], asm_blocks=[asm_block])

    _conditions(hll, asm, result, add)

    # ---- returns. A "return path" is a way out of the function: after rule N4, an edge into EXIT.
    hll_returns = _returning_blocks(hll)
    asm_returns = _returning_blocks(asm)
    lost = [b for b in hll_returns if pairs.get(b) is not None and pairs[b] not in asm_returns]
    if lost:
        add("MISSING_RETURN", "error",
            f"{len(lost)} return path(s) of the C program do not return in the assembly: "
            f"{', '.join(f'{b} -> {pairs[b]}' for b in lost)}",
            hll_blocks=lost, asm_blocks=[pairs[b] for b in lost],
            hll_returns=len(hll_returns), asm_returns=len(asm_returns))
    elif len(hll_returns) > len(asm_returns):
        add("MISSING_RETURN", "error",
            f"the C program returns on {len(hll_returns)} path(s) and the assembly on {len(asm_returns)}",
            hll_blocks=hll_returns, asm_blocks=asm_returns,
            hll_returns=len(hll_returns), asm_returns=len(asm_returns))

    # ---- behaviours the assembly has and the C program does not
    left_paths, right_paths = _path_behaviours(hll), _path_behaviours(asm)
    if left_paths is not None and right_paths is not None:
        for behaviour in sorted(right_paths - left_paths)[:3]:
            add("UNEXPECTED_PATH", "error",
                f"the assembly can execute a sequence the C program cannot: "
                f"{' '.join(behaviour) or '(an empty path)'}",
                path=list(behaviour))

    # ---- anything an L1 invariant flagged that nothing above explains
    _unexplained_invariants(result, findings, add)

    if not findings:
        add("STRUCTURAL_MISMATCH", "error",
            "the graphs are not isomorphic, but every invariant, block, edge and return lines up; "
            "the difference is in how the blocks are connected",
            invariant="isomorphism")
    return _sorted(findings)


def _returning_blocks(cfg: CFG) -> list[str]:
    """The blocks from which the function is left: the predecessors of EXIT, or the exits themselves."""
    exits = {n.id for n in cfg.nodes if not cfg.out_edges(n.id)}
    sources = [e.source for e in cfg.edges if e.destination in exits]
    return sorted(set(sources)) if sources else sorted(exits)


def _unsupported(hll: CFG, asm: CFG, add) -> None:
    """Constructs outside the supported subset, on either side. Always reported, never a silent pass."""
    for cfg, side in ((hll, "HLL"), (asm, "assembly")):
        for kind in cfg.analysis.get("unsupported", []):
            blocks = [n.id for n in cfg.nodes if n.terminator == kind]
            add("UNSUPPORTED_CONSTRUCT", "warning",
                f"the {side} CFG contains {kind!r}, which is outside the supported subset",
                hll_blocks=blocks if side == "HLL" else (), asm_blocks=blocks if side == "assembly" else (),
                construct=kind, side=side)


def _unexplained_invariants(result: MatchResult, findings: list[Finding], add) -> None:
    """An L1 disagreement that no located finding already accounts for."""
    kinds = {f.kind for f in findings}
    for name, left_value, right_value in result.invariant_diffs:
        if name == "unsupported":
            continue                                   # _unsupported() already said it, with the blocks
        if any(k in kinds for k in _EXPLAINED_BY.get(name, ())):
            continue
        add("STRUCTURAL_MISMATCH", "error",
            f"{name}: the C program has {left_value!r} and the assembly {right_value!r}",
            invariant=name, hll=repr(left_value), asm=repr(right_value))


def _conditions(hll: CFG, asm: CFG, result: MatchResult, add) -> None:
    for hll_block, asm_block, left_key, right_key in result.condition_diffs:
        add("WRONG_BRANCH_CONDITION", "warning",
            f"the decision in {hll_block} tests {left_key or '(unknown)'} and the assembly block "
            f"{asm_block} tests {right_key or '(unknown)'}",
            hll_blocks=[hll_block], asm_blocks=[asm_block], hll_key=left_key, asm_key=right_key)


def _index(cfg: CFG, block_id: str) -> int:
    try:
        return cfg.node(block_id).index
    except KeyError:
        return 1 << 30


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (_ORDER.index(f.kind) if f.kind in _ORDER else 99, f.id))
