"""Part 6 checkpoint: **prove that normalization did not lose control-flow meaning**.

The rules in `rules.py` could be wrong. So nothing here trusts them: every check is computed from the raw CFG
and from the normalized CFG independently, and the two answers must agree. This is the same discipline used in
Parts 3–5 (LLVM's own dot-cfg, the compiler's block marks, the assembler) — a rewrite is only believed when
something that did not perform it says the result is right.

What must survive, and how it is checked:

| # | Property | Check |
|---|----------|-------|
| P1 | **What the program computes, in order, on every path.** | Enumerate every simple entry→exit path in both CFGs; reduce each to the sequence of semantic operations it performs (`normalize/semantics.py`), dropping pure control transfers and the virtual nodes. The two sets of sequences must be equal. |
| P2 | **Which code can repeat.** | Every raw block that was inside a loop must still be inside one, and every block that was not must still not be. Nesting may not get deeper, and loops may not all vanish. (The loop *count* is deliberately not checked: splicing out an empty outer-loop header correctly turns two nested loops into one.) |
| P3 | **Decisions.** | Every decision in the raw CFG must still be a decision, except the ones a logged N5/N8 step removed, and the polarity-free condition keys must match. |
| P4 | **Reachability between surviving blocks.** | For every pair of raw blocks that both survive, "u can reach v" must have the same answer in both CFGs. |
| P5 | **Dominance between surviving blocks.** | Likewise for "u dominates v": normalization must not reorder control. |
| P6 | **Nothing is invented or silently dropped.** | Every normalized block's `origin_blocks` are raw blocks, each raw block appears at most once, and every raw block is either accounted for by a logged step or present. |

P1 is the strong one: it is a statement about *executions*, not about graph shape. P4–P6 catch the cases where
path enumeration cannot (a loop whose body a rule reordered still has the same simple paths).

Path enumeration is bounded (`max_paths`); for the CFGs in this project — up to 13 blocks — it terminates at
once, and when a CFG is too large the result says P1 was skipped rather than silently passing.
"""

from __future__ import annotations

import networkx as nx

from cfgverify.model.cfg import CFG
from cfgverify.normalize import conditions as cond
from cfgverify.normalize import loops as loop_shapes
from cfgverify.normalize import semantics
from cfgverify.normalize.rules import EXIT_ID, TRAP_ID, NormalizationStep

VIRTUAL = (EXIT_ID, TRAP_ID)
LOOP_SINK = "#LOOP"          # where a back edge leads in the acyclic view: "round the loop again"


# ------------------------------------------------------------------------------------------ helpers
def _content(cfg: CFG, node_id: str) -> tuple[str, ...]:
    if node_id in VIRTUAL or node_id == LOOP_SINK:
        return ()
    return semantics.content_signature(cfg.node(node_id).operations)


def _acyclic_view(cfg: CFG) -> nx.DiGraph:
    """One iteration of every loop: each back edge is redirected to a single synthetic sink, `#LOOP`.

    Paths are enumerated here rather than on the cyclic graph because `all_simple_paths` on a cyclic graph
    refuses to revisit a node, which makes the path set depend on where merge blocks happen to sit — exactly
    the accident normalization removes. Back edges are *redirected* rather than deleted so that "this path
    goes round the loop again" stays a distinct, comparable outcome: removing a jump-only block can turn one
    back edge into two (Part 2, observation O5), and with deletion that would silently truncate a path.
    """
    g = nx.DiGraph()
    g.add_nodes_from(n.id for n in cfg.nodes)
    g.add_node(LOOP_SINK)
    for edge in cfg.edges:
        g.add_edge(edge.source, LOOP_SINK if edge.back_edge else edge.destination)
    return g


def _path_sequences(cfg: CFG, max_paths: int) -> set[tuple[str, ...]] | None:
    """Every path of the acyclic view, reduced to what it computes. None when the bound was hit."""
    g = _acyclic_view(cfg)
    targets = [n for n in g.nodes if g.out_degree(n) == 0]
    sequences: set[tuple[str, ...]] = set()
    for target in targets:
        for path in nx.all_simple_paths(g, cfg.entry, target):
            sequences.add(tuple(op for node in path for op in _content(cfg, node)))
            if len(sequences) > max_paths:
                return None
    sequences.add(tuple(_content(cfg, cfg.entry)) if cfg.entry in targets else ())
    sequences.discard(())
    return sequences


def _reachability(cfg: CFG) -> dict[str, set[str]]:
    g = cfg.to_networkx(include_attributes=False)
    return {n.id: set(nx.descendants(g, n.id)) for n in cfg.nodes}


def _dominators(cfg: CFG) -> dict[str, set[str]]:
    """dominators[v] = the blocks that dominate v (computed by networkx, not by our own code)."""
    g = cfg.to_networkx(include_attributes=False)
    reachable = {cfg.entry} | nx.descendants(g, cfg.entry)
    idom = nx.immediate_dominators(g.subgraph(reachable), cfg.entry)
    doms: dict[str, set[str]] = {}
    for node in reachable:
        chain, current = set(), node
        while True:                                  # walk up the dominator tree to the entry
            chain.add(current)
            parent = idom.get(current, cfg.entry)    # some networkx versions omit the entry's own entry
            if parent == current or current == cfg.entry:
                break
            current = parent
        doms[node] = chain
    return doms


def _origin_map(after: CFG) -> dict[str, str]:
    """raw block label -> the normalized block that now contains it."""
    mapping: dict[str, str] = {}
    for node in after.nodes:
        for label in (node.origin_blocks or [node.label]):
            mapping.setdefault(label, node.id)
    return mapping


def _label_of(before: CFG) -> dict[str, str]:
    return {n.id: n.label for n in before.nodes}


# ------------------------------------------------------------------------------------------- checks
def check_preserved(before: CFG, after: CFG, log: list[NormalizationStep] | None = None,
                    max_paths: int = 20000) -> list[str]:
    """Returns the list of properties that FAILED. An empty list means meaning was preserved."""
    log = log or []
    problems: list[str] = []

    folds = [s for s in log if s.rule in ("N5", "N8")]

    # ---- P1: what every path computes.
    # A fold (N5/N8) deletes an arm that could never be taken, so the normalized path set may be a strict
    # SUBSET after one. It may never contain a behaviour the raw CFG did not have.
    left, right = _path_sequences(before, max_paths), _path_sequences(after, max_paths)
    if left is None or right is None:
        problems.append("P1 skipped: too many paths to enumerate (raise max_paths to check this CFG)")
    else:
        invented = sorted(right - left)[:2]
        if invented:
            problems.append(f"P1 normalization invented {len(right - left)} path behaviour(s), "
                            f"e.g. {invented}")
        elif not folds and left != right:
            problems.append(f"P1 path semantics changed with no fold step logged: {len(left)} raw vs "
                            f"{len(right)} normalized; lost e.g. {sorted(left - right)[:2]}")

    # ---- P2: loop structure.
    # The signature itself is the form used to COMPARE the two sides; as a preservation check it is too
    # strict. Removing a jump-only merge block legitimately turns one back edge into two (Part 2,
    # observation O5), a fold legitimately removes a dead loop exit, and — the subtle one — when the header
    # of an outer loop is an empty block, splicing it out gives the outer and the inner loop the same header,
    # so two nested loops correctly become one. What must survive is the property the loop count is a proxy
    # for: **which code can repeat**. Every raw block that was inside some loop must still be inside one,
    # and every raw block that was not must still not be.
    label_of = _label_of(before)
    where = _origin_map(after)
    depth_after = {n.id: n.loop_depth for n in after.nodes}
    for node in before.nodes:
        target = where.get(label_of[node.id])
        if target is None:
            continue                                  # the block itself was removed; P6 accounts for that
        was_in_loop, is_in_loop = node.loop_depth > 0, depth_after.get(target, 0) > 0
        if was_in_loop != is_in_loop:
            problems.append(f"P2 block {label_of[node.id]} was {'inside' if was_in_loop else 'outside'} a "
                            f"loop and is now {'inside' if is_in_loop else 'outside'} one")
            break
    raw_max = max((loop["depth"] for loop in before.analysis.get("loops", [])), default=0)
    new_max = max((loop["depth"] for loop in after.analysis.get("loops", [])), default=0)
    if new_max > raw_max:
        problems.append(f"P2 loop nesting got deeper: {raw_max} -> {new_max}")
    if raw_max and not new_max:
        problems.append("P2 every loop disappeared")

    # ---- P3: decisions
    folded = len(folds)
    raw_decisions = before.analysis.get("decisions", 0)
    new_decisions = after.analysis.get("decisions", 0)
    if new_decisions != raw_decisions - folded:
        problems.append(f"P3 decision count {raw_decisions} -> {new_decisions}, but {folded} fold step(s) "
                        f"were logged (expected {raw_decisions - folded})")
    raw_keys = sorted(cond.decision_key(before, n.id, polarity_free=True)
                      for n in before.nodes if len(before.out_edges(n.id)) >= 2)
    new_keys = sorted(cond.decision_key(after, n.id, polarity_free=True)
                      for n in after.nodes if len(after.out_edges(n.id)) >= 2)
    if folded == 0 and raw_keys != new_keys:
        problems.append(f"P3 conditions changed: {raw_keys} -> {new_keys}")

    # ---- P4/P5: reachability and dominance among surviving raw blocks
    label_of = _label_of(before)
    where = _origin_map(after)
    survivors = [n.id for n in before.nodes if label_of[n.id] in where]
    reach_before, reach_after = _reachability(before), _reachability(after)
    dom_before, dom_after = _dominators(before), _dominators(after)
    for u in survivors:
        for v in survivors:
            if u == v:
                continue
            nu, nv = where[label_of[u]], where[label_of[v]]
            if nu == nv:
                continue
            if (v in reach_before[u]) != (nv in reach_after[nu]):
                problems.append(f"P4 reachability changed for {label_of[u]} -> {label_of[v]}")
                break
            if u in dom_before and v in dom_before and nu in dom_after and nv in dom_after:
                if (u in dom_before[v]) != (nu in dom_after[nv]):
                    problems.append(f"P5 dominance changed for {label_of[u]} over {label_of[v]}")
                    break
        else:
            continue
        break

    # ---- P6: provenance is complete and injective
    seen: dict[str, str] = {}
    for node in after.nodes:
        for label in (node.origin_blocks or []):
            if label in VIRTUAL:
                continue
            if label in seen:
                problems.append(f"P6 raw block {label} appears in both {seen[label]} and {node.id}")
            seen[label] = node.id
    raw_labels = {n.label for n in before.nodes}
    removed = raw_labels - set(seen)
    explained = {b for step in log for b in step.origin_blocks} | {b for step in log for b in step.blocks}
    for label in sorted(removed):
        if label not in explained:
            problems.append(f"P6 raw block {label} disappeared with no rule step to account for it")
    invented = {label for label in seen if label not in raw_labels and label not in VIRTUAL}
    if invented:
        problems.append(f"P6 normalized blocks claim raw blocks that do not exist: {sorted(invented)}")

    return problems


def check_normalized(cfg: CFG) -> list[str]:
    """The generic CFG checks (schema, round trips, invariants) applied to a normalized CFG."""
    from cfgverify.hll.cfg_checks import check_cfg

    problems = check_cfg(cfg)
    exits = [n.id for n in cfg.nodes if not cfg.out_edges(n.id)]
    if "N4" in (cfg.meta.get("rules_applied") or []) and len(exits) > 1:
        problems.append(f"rule N4 ran but there are {len(exits)} exit blocks: {exits}")
    for node in cfg.nodes:
        if not node.origin_blocks:
            problems.append(f"{node.id}: no provenance (origin_blocks is empty)")
    return problems


def summary(before: CFG, after: CFG, log: list[NormalizationStep]) -> dict:
    """The numbers the checkpoint prints: what shrank, what stayed the same."""
    return {
        "blocks": (len(before.nodes), len(after.nodes)),
        "edges": (len(before.edges), len(after.edges)),
        "decisions": (before.analysis.get("decisions"), after.analysis.get("decisions")),
        "loops": (len(before.analysis.get("loops", [])), len(after.analysis.get("loops", []))),
        "loop_signature": loop_shapes.loop_signature(after),
        "steps": len(log),
        "rules": sorted({s.rule for s in log}),
    }
