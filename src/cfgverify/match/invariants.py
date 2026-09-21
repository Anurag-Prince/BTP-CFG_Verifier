"""Matcher level **L1**: cheap structural fingerprints, computed identically for both sides.

Before attempting graph isomorphism — which is the expensive and fragile step — compare a handful of numbers
that any two isomorphic CFGs must agree on. If they disagree, the graphs cannot be isomorphic, we know
*which* number disagreed, and that is already a located, quotable piece of evidence.

None of these is a verdict. They are inputs to `match/diagnose.py`, which turns a disagreement into a
`Finding`, and to `report/verdict.py`, which is the only place a verdict is decided.
"""

from __future__ import annotations

from cfgverify.model.cfg import CFG
from cfgverify.normalize import loops as loop_shapes
from cfgverify.normalize import semantics

#: Each entry: the name a reader sees, and how to compute it from a normalized CFG.
INVARIANTS = (
    "blocks", "edges", "decisions", "cyclomatic", "loops", "max_loop_depth",
    "exits", "returns", "back_edges", "unsupported",
)


def fingerprint(cfg: CFG) -> dict:
    """The L1 fingerprint of one normalized CFG. Order-independent, name-independent."""
    analysis = cfg.analysis
    loops = analysis.get("loops", [])
    exits = [n.id for n in cfg.nodes if not cfg.out_edges(n.id)]
    # How many ways the function can be left. After rule N4 every return is wired to one EXIT node, so the
    # number of RETURN PATHS is the number of edges into it — not the number of `ret` instructions, which
    # Clang -O0 always reduces to one however many `return` statements the C had.
    returns = [e for e in cfg.edges if e.destination in exits] or \
        [n for n in cfg.nodes if n.terminator in ("ret", "falls_off_end")]
    return {
        "blocks": len(cfg.nodes),
        "edges": len(cfg.edges),
        "decisions": analysis.get("decisions", 0),
        "cyclomatic": analysis.get("cyclomatic", 0),
        "loops": len(loops),
        "max_loop_depth": max((loop["depth"] for loop in loops), default=0),
        "exits": len(exits),
        "returns": len(returns),
        "back_edges": len(analysis.get("back_edges", [])),
        "unsupported": list(analysis.get("unsupported", [])),   # a list, not a tuple: JSON has no tuples
        # not compared by `disagreements()`; carried for the report and for L3
        "loop_signature": loop_shapes.loop_signature(cfg),
        "content_profile": content_profile(cfg),
    }


def content_profile(cfg: CFG) -> dict[str, int]:
    """How many semantic operations of each category the whole function performs."""
    total: dict[str, int] = {}
    for node in cfg.nodes:
        for kind, count in semantics.content_profile(node.operations).items():
            total[kind] = total.get(kind, 0) + count
    return dict(sorted(total.items()))


def disagreements(hll: dict, asm: dict) -> list[tuple[str, object, object]]:
    """Which invariants differ, in a fixed order. Empty means L1 found no reason to reject."""
    return [(name, hll[name], asm[name]) for name in INVARIANTS if hll[name] != asm[name]]


def node_signature(cfg: CFG, node_id: str, *, with_content: bool = True) -> tuple:
    """What must be true of a block for it to be a candidate counterpart of another block.

    Structure first — a block's role in the graph — and then, at level **L3**, what it computes. Names, labels,
    registers, line numbers and instruction text are never part of it. The `T`/`F` arms of a decision are
    folded to `cond`, because which arm a compiler puts first is its own choice (rule N6); the arms are
    compared by the edges' polarity-free condition keys in `match/matcher.py`.
    """
    node = cfg.node(node_id)
    outs = cfg.out_edges(node_id)
    structure = (
        len(outs),                                     # 0 = exit, 1 = straight, 2 = decision, n = switch
        len(node.predecessors),
        _terminator_class(node.terminator),
        node.is_entry,
        not outs,
        node.loop_header,
        node.loop_depth,
        tuple(sorted("cond" if e.kind in ("T", "F") else e.kind for e in outs)),
        sum(1 for e in outs if e.back_edge),
    )
    if not with_content:
        return structure
    return structure + (semantics.content_signature(node.operations),)


def _terminator_class(terminator: str) -> str:
    """Terminator names differ between the sides (`ret` vs `falls_off_end`); their meaning does not."""
    return {
        "cond_br": "decision", "br": "jump", "ret": "return", "falls_off_end": "return",
        "unreachable": "noreturn", "indirect": "indirect", "br_external": "tailcall",
    }.get(terminator, terminator)
