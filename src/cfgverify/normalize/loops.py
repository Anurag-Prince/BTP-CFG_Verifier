"""Part 6, layer 3: **loop structure in a comparable form**.

`CFG.compute_analysis()` (Part 4) already finds back edges, natural loops and nesting depth with the dominator
algorithm in `graph_utils.py`. What Part 6 adds is a description of that structure which contains **no block
names**, so the loop nest of an LLVM CFG and the loop nest of an assembly CFG can be put side by side.

A loop is described by what is true of it regardless of who compiled it:

    depth            how deeply it is nested
    exits            how many distinct blocks inside it can leave it (1 = a plain loop, 2+ = `break`)
    back_edges       how many back edges reach its header (2+ = `continue` in a `while`, observation O5)
    children         the loops nested directly inside it, in the same form

The forest of those descriptions is the loop signature. Two CFGs with the same signature have the same loop
nesting, the same number of loops at each level, the same break/continue shape — with no name ever compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoopShape:
    depth: int
    exits: int
    back_edges: int
    children: tuple["LoopShape", ...] = field(default=())

    def key(self) -> str:
        inner = ",".join(sorted(c.key() for c in self.children))
        return f"L(d{self.depth},x{self.exits},b{self.back_edges}[{inner}])"


def loop_forest(cfg) -> tuple[LoopShape, ...]:
    """The nesting forest of `cfg.analysis["loops"]`, described without block names."""
    loops = cfg.analysis.get("loops") or []
    bodies = {loop["header"]: set(loop["body"]) for loop in loops}
    depth_of = {loop["header"]: loop["depth"] for loop in loops}

    def parent_of(header: str) -> str | None:
        """The innermost other loop that contains this one."""
        candidates = [h for h, body in bodies.items()
                      if h != header and header in body and depth_of[h] < depth_of[header]]
        return max(candidates, key=lambda h: depth_of[h]) if candidates else None

    children: dict[str | None, list[str]] = {}
    for header in bodies:
        children.setdefault(parent_of(header), []).append(header)

    def shape(header: str) -> LoopShape:
        body = bodies[header]
        exits = sum(1 for n in body if any(e.destination not in body for e in cfg.out_edges(n)))
        backs = sum(1 for e in cfg.edges if e.destination == header and e.back_edge)
        return LoopShape(depth_of[header], exits, backs,
                         tuple(shape(c) for c in sorted(children.get(header, []))))

    return tuple(sorted((shape(h) for h in children.get(None, [])), key=lambda s: s.key()))


def loop_signature(cfg) -> str:
    """One string. Equal signatures mean the same loop nesting, break and continue shape."""
    forest = loop_forest(cfg)
    return "{" + ",".join(s.key() for s in forest) + "}" if forest else "{}"
