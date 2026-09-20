"""Graph facts about a CFG that don't depend on where the CFG came from (HLL, assembly or a hand-written spec).

Part 2 uses these to check the expected CFGs of the regression programs. The matcher's L1 invariants
(docs/ARCHITECTURE.md §7.5) will reuse them later.

Definitions used here (the standard ones, as in the "Dragon Book"):
  * d dominates n  if every path from the entry to n passes through d.
  * back edge      an edge t -> h where h dominates t. h is the loop HEADER and t is a LATCH.
  * natural loop   for header h: h plus every node that can reach one of h's latches without passing through h.
  * loop depth     how many natural loops contain a node (1 = outermost loop).
Loops made with goto can be "irreducible" and have no back edge in this sense. That is outside the current subset.
"""

from __future__ import annotations

import networkx as nx


def simple_digraph(g: nx.DiGraph | nx.MultiDiGraph) -> nx.DiGraph:
    """Collapse parallel edges (a conditional branch whose two targets are the same block)."""
    d = nx.DiGraph()
    d.add_nodes_from(g.nodes)
    d.add_edges_from((u, v) for u, v, *_ in g.edges)
    return d


def dominates(idom: dict, a, b) -> bool:
    """True if a dominates b, using an immediate-dominator map from networkx.immediate_dominators."""
    node = b
    while True:
        if node == a:
            return True
        parent = idom.get(node)
        if parent is None or parent == node:
            return False
        node = parent


def back_edges(g, entry) -> list[tuple]:
    """All edges t -> h such that h dominates t. Sorted, so results are deterministic."""
    d = simple_digraph(g)
    idom = nx.immediate_dominators(d, entry)
    return sorted((t, h) for t, h in d.edges if t in idom and dominates(idom, h, t))


def natural_loops(g, entry) -> dict:
    """header -> set of blocks in the natural loop (loops that share a header are merged)."""
    d = simple_digraph(g)
    loops: dict = {}
    for latch, header in back_edges(d, entry):
        body = loops.setdefault(header, {header})
        stack = [latch]
        while stack:
            node = stack.pop()
            if node not in body:
                body.add(node)
                stack.extend(d.predecessors(node))
    return loops


def loop_depths(g, entry) -> dict:
    """header -> nesting depth (1 = outermost)."""
    loops = natural_loops(g, entry)
    return {h: sum(1 for body in loops.values() if h in body) for h in loops}


def cyclomatic(g) -> int:
    """McCabe cyclomatic complexity E - N + 2 for one connected CFG (parallel edges count separately)."""
    return g.number_of_edges() - g.number_of_nodes() + 2
