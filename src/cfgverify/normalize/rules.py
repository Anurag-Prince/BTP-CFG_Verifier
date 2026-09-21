"""Part 6, layer 4: **CFG normalization rules N1–N8**, with a replayable log.

The two raw CFGs differ in ways that have nothing to do with what the program does: Clang `-O0` leaves
jump-only blocks behind, the assembler splits a block wherever a label happens to sit, one side funnels every
`return` into a single `ret` and the other has several, and a `do { } while (1)` is a real test in the IR and a
plain `jmp` in GCC's assembly. Normalization removes exactly those differences and **nothing else**.

    N1  unreachable removal        delete blocks the entry cannot reach
    N2  jump-only bypass           a block that only jumps is spliced out, its predecessors retargeted
    N3  chain merge                A -> B with one successor and one predecessor become one block
    N4  single virtual EXIT        every return is wired to one EXIT node
    N5  degenerate branch          a two-way branch whose arms are the same block becomes a jump
    N6  polarity-free conditions   annotate each conditional edge with the key that ignores arm order
    N7  noreturn sink              blocks that end `unreachable` are wired to one TRAP node
    N8  constant-condition fold    `br i1 true` becomes a jump to the live arm

Contract, and every part of it is tested:

* **Identical on both sides.** Nothing in this file reads `cfg.origin`, the ISA, or any file name.
* **Semantics-preserving for control flow.** Proven per run by `normalize/checks.py`, which compares the
  before and after CFGs on paths, loops and dominance rather than trusting the rules.
* **Logged.** Every application produces a `NormalizationStep`, so a reviewer can replay the whole run, and
  every normalized block records the raw blocks it came from (`origin_blocks`).
* **Run to a fixpoint**, in an order chosen so each rule feeds the next (N8/N5 create jumps, which N2 then
  removes, which N3 then merges).

The rule *set* is a proposal until **[C5]** is confirmed. `normalize(..., rules=...)` can switch any of them
off, so the effect of each one is measurable rather than asserted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from cfgverify import __version__
from cfgverify.model.cfg import CFG, BranchInfo, Edge, InstructionRecord, Node
from cfgverify.model.ops import Operation
from cfgverify.normalize import conditions as cond
from cfgverify.normalize import loops as loop_shapes
from cfgverify.normalize import semantics

ALL_RULES = ("N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8")
DEFAULT_RULES = ALL_RULES
EXIT_ID, TRAP_ID = "EXIT", "TRAP"
GENERATOR = f"cfgverify {__version__} normalize.rules"

RULE_NAMES = {
    "N1": "unreachable removal", "N2": "jump-only bypass", "N3": "chain merge",
    "N4": "single virtual EXIT", "N5": "degenerate branch -> jump", "N6": "polarity-free conditions",
    "N7": "noreturn -> TRAP sink", "N8": "constant-condition branch fold",
}


@dataclass
class NormalizationStep:
    """One rule application, in enough detail to replay and to quote in a report."""
    index: int
    rule: str
    name: str
    blocks: list[str]                       # the blocks of the CFG *as it was* when the rule fired
    origin_blocks: list[str] = field(default_factory=list)   # the raw block labels behind them
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"{self.index:>3}. {self.rule} {self.name}: {self.detail}"


# ------------------------------------------------------------------------------------------- helpers
def _jump(target: str) -> Operation:
    return Operation("JUMP", None, None, (target,), f"JUMP {target}")


def _is_jump_only(node: Node) -> bool:
    """A block that transfers control and does nothing else (Clang -O0 `if.end` pads, assembly jump pads)."""
    kinds = [semantics.semantic_class(o) for o in node.operations]
    return bool(kinds) and all(k in semantics.CONTROL_ONLY for k in kinds)


def _relabel(cfg: CFG) -> CFG:
    """Renumber to B0..Bn-1 in reachable-first order, rebuild predecessor/successor lists and edge ids."""
    order = {n.id: i for i, n in enumerate(cfg.nodes)}
    cfg.nodes.sort(key=lambda n: order[n.id])
    rename = {n.id: (n.id if n.id in (EXIT_ID, TRAP_ID) else f"B{i}") for i, n in enumerate(cfg.nodes)}
    for index, node in enumerate(cfg.nodes):
        node.id, node.index = rename[node.id], index
        node.is_entry = index == 0
    for node in cfg.nodes:
        node.successors = [rename[s] for s in node.successors]
        node.predecessors = [rename[p] for p in node.predecessors]
    for edge in cfg.edges:
        edge.source, edge.destination = rename[edge.source], rename[edge.destination]
    cfg.entry = rename[cfg.entry]
    cfg.exits = [rename[e] for e in cfg.exits]
    cfg.edges.sort(key=lambda e: (cfg.node(e.source).index, e.index))
    for i, edge in enumerate(cfg.edges):
        edge.id = f"E{i}"
    return _rewire(cfg)


def _rewire(cfg: CFG) -> CFG:
    """Recompute successor/predecessor lists, edge indices, exits and the exit flags from the edges alone."""
    by_id = {n.id: n for n in cfg.nodes}
    for node in by_id.values():
        node.successors, node.predecessors = [], []
    for node in cfg.nodes:
        outs = [e for e in cfg.edges if e.source == node.id]
        outs.sort(key=lambda e: e.index)
        for i, edge in enumerate(outs):
            edge.index = i
        node.successors = list(dict.fromkeys(e.destination for e in outs))
    for edge in cfg.edges:
        if edge.source not in by_id[edge.destination].predecessors:
            by_id[edge.destination].predecessors.append(edge.source)
    cfg.exits = [n.id for n in cfg.nodes if not n.successors]
    for node in cfg.nodes:
        node.is_exit = node.id in cfg.exits
    return cfg


def _drop_nodes(cfg: CFG, ids: set[str]) -> None:
    cfg.nodes = [n for n in cfg.nodes if n.id not in ids]
    cfg.edges = [e for e in cfg.edges if e.source not in ids and e.destination not in ids]


# ----------------------------------------------------------------------------------------- the rules
def _n8_constant_condition(cfg: CFG) -> list[tuple[str, str]]:
    """`br i1 true` / a branch whose condition is a literal: keep only the arm that is actually taken."""
    fired = []
    for node in list(cfg.nodes):
        outs = cfg.out_edges(node.id)
        if node.terminator != "cond_br" or len(outs) != 2:
            continue
        branch = outs[0].branch
        literal = (branch.condition or "").strip().lower() if branch else ""
        if literal not in ("true", "false", "1", "0"):
            continue
        live, dead = (outs[0], outs[1]) if literal in ("true", "1") else (outs[1], outs[0])
        cfg.edges.remove(dead)
        live.kind, live.index, live.branch = "U", 0, None
        node.terminator = "br"
        if node.operations and node.operations[-1].op == "BRANCH":
            node.operations[-1] = _jump(live.destination)
        fired.append((node.id, list(node.origin_blocks),
                      f"condition is the literal {literal}; kept the "
                      f"{'true' if literal in ('true', '1') else 'false'} arm"))
    return fired


def _n5_degenerate_branch(cfg: CFG) -> list[tuple[str, str]]:
    """A two-way branch whose arms lead to the same block is not a decision."""
    fired = []
    for node in list(cfg.nodes):
        outs = cfg.out_edges(node.id)
        if len(outs) >= 2 and len({e.destination for e in outs}) == 1:
            for extra in outs[1:]:
                cfg.edges.remove(extra)
            outs[0].kind, outs[0].index, outs[0].branch = "U", 0, None
            node.terminator = "br"
            if node.operations and node.operations[-1].op in ("BRANCH", "SWITCH"):
                node.operations[-1] = _jump(outs[0].destination)
            fired.append((node.id, list(node.origin_blocks),
                          f"all {len(outs)} arms lead to {outs[0].destination}"))
    return fired


def _n1_unreachable(cfg: CFG) -> list[tuple[str, str]]:
    import networkx as nx
    g = cfg.to_networkx(include_attributes=False)
    reachable = {cfg.entry} | nx.descendants(g, cfg.entry)
    dead = {n.id for n in cfg.nodes if n.id not in reachable}
    if not dead:
        return []
    labels = {n.id: list(n.origin_blocks) for n in cfg.nodes if n.id in dead}
    _drop_nodes(cfg, dead)
    return [(node, labels.get(node, []), "not reachable from the entry") for node in sorted(dead)]


def _n2_jump_only(cfg: CFG) -> list[tuple[str, str]]:
    """Splice out a block that only jumps: every edge into it is retargeted to where it jumps."""
    fired = []
    for node in list(cfg.nodes):
        outs = cfg.out_edges(node.id)
        if (node.id == cfg.entry or node.id in (EXIT_ID, TRAP_ID) or len(outs) != 1
                or not _is_jump_only(node)):
            continue
        target = outs[0].destination
        if target == node.id:                               # a one-block infinite loop: real control flow
            continue
        incoming = [e for e in cfg.edges if e.destination == node.id]
        if any(e.source == node.id for e in incoming):
            continue
        for edge in incoming:
            edge.destination = target
        cfg.edges.remove(outs[0])
        _drop_nodes(cfg, {node.id})
        fired.append((node.id, list(node.origin_blocks),
                      f"only transfers control; {len(incoming)} edge(s) retargeted to {target}"))
    return fired


def _n3_chain_merge(cfg: CFG) -> list[tuple[str, str]]:
    """A -> B, where A has one successor and B has one predecessor: one block, in order."""
    fired = []
    changed = True
    while changed:
        changed = False
        for a in list(cfg.nodes):
            outs = cfg.out_edges(a.id)
            if len(outs) != 1 or a.id in (EXIT_ID, TRAP_ID):
                continue
            b_id = outs[0].destination
            if b_id in (EXIT_ID, TRAP_ID, cfg.entry, a.id):
                continue
            b = cfg.node(b_id)
            if len({e.source for e in cfg.edges if e.destination == b_id}) != 1:
                continue
            if len([e for e in cfg.edges if e.destination == b_id]) != 1:
                continue
            keep = [o for o in a.operations if semantics.semantic_class(o) not in semantics.CONTROL_ONLY]
            dropped = len(a.operations) - len(keep)     # the jump that no longer exists
            a.operations = keep + list(b.operations)
            a.instructions = (list(a.instructions)[:len(a.instructions) - dropped] + list(b.instructions))
            a.source_lines = sorted(set(a.source_lines) | set(b.source_lines))
            a.calls = list(a.calls) + list(b.calls)
            a.terminator = b.terminator
            a.label = f"{a.label}+{b.label}"
            a.loop_header = a.loop_header or b.loop_header
            cfg.edges.remove(outs[0])
            for edge in cfg.edges:
                if edge.source == b_id:
                    edge.source = a.id
            _merge_origin(a, b)
            _drop_nodes(cfg, {b_id})
            fired.append((f"{a.id}+{b_id}", list(a.origin_blocks),
                          f"{b_id} has one predecessor and {a.id} one successor"))
            changed = True
            break
    return fired


def _n7_noreturn_sink(cfg: CFG) -> list[tuple[str, str]]:
    ends = [n for n in cfg.nodes if n.terminator == "unreachable" and not cfg.out_edges(n.id)]
    if not ends:
        return []
    if not any(n.id == TRAP_ID for n in cfg.nodes):
        cfg.nodes.append(_virtual_node(TRAP_ID, len(cfg.nodes), "UNREACHABLE", "unreachable"))
    for node in ends:
        cfg.edges.append(Edge(f"E{len(cfg.edges)}", node.id, TRAP_ID, 0, "U"))
    return [(n.id, list(n.origin_blocks), "control never leaves this block; wired to the TRAP sink")
            for n in ends]


def _n4_single_exit(cfg: CFG) -> list[tuple[str, str]]:
    ends = [n for n in cfg.nodes if not cfg.out_edges(n.id) and n.id not in (EXIT_ID, TRAP_ID)]
    if not ends:
        return []
    if not any(n.id == EXIT_ID for n in cfg.nodes):
        cfg.nodes.append(_virtual_node(EXIT_ID, len(cfg.nodes), "RETURN", "ret"))
    for node in ends:
        cfg.edges.append(Edge(f"E{len(cfg.edges)}", node.id, EXIT_ID, 0, "U"))
    return [(n.id, list(n.origin_blocks), "return path wired to the single virtual EXIT") for n in ends]


def _n6_polarity_free(cfg: CFG) -> list[tuple[str, str]]:
    """Annotate: every conditional edge gets its comparable key and the polarity-free key (no rewrite)."""
    fired = []
    for node in cfg.nodes:
        outs = cfg.out_edges(node.id)
        if len(outs) < 2:
            continue
        for edge in outs:
            condition = cond.condition_of_edge(cfg, edge)
            if edge.branch is None:
                edge.branch = BranchInfo(taken_when=f"{edge.kind} arm")
            edge.branch.condition_key = condition.key
            edge.branch.polarity_free_key = condition.polarity_free_key()
        fired.append((node.id, list(node.origin_blocks),
                      f"decision keyed as {cond.decision_key(cfg, node.id, polarity_free=True)}"))
    return fired


def _virtual_node(node_id: str, index: int, op: str, terminator: str) -> Node:
    """EXIT (rule N4) and TRAP (rule N7): they hold no instruction of the program, only the structure."""
    operation = Operation(op, None, None, (), op)
    return Node(id=node_id, label=node_id, index=index, instructions=[InstructionRecord(node_id, None)],
                operations=[operation], predecessors=[], successors=[], terminator=terminator,
                is_exit=True, origin_blocks=[node_id])


def _merge_origin(into: Node, other: Node) -> None:
    into.origin_blocks = (into.origin_blocks or [into.label]) + (other.origin_blocks or [other.label])


# ------------------------------------------------------------------------------------------ the pass
_RULE_FUNCTIONS = {
    "N8": _n8_constant_condition, "N5": _n5_degenerate_branch, "N1": _n1_unreachable,
    "N2": _n2_jump_only, "N3": _n3_chain_merge, "N7": _n7_noreturn_sink, "N4": _n4_single_exit,
    "N6": _n6_polarity_free,
}
#: N8/N5 make jumps, N1 clears dead blocks, N2 removes the jumps, N3 merges what is left; then the virtual
#: nodes go on, and N6 keys the decisions that survived.
_ORDER = ("N8", "N5", "N1", "N2", "N3")
_FINAL = ("N7", "N4", "N6")


def normalize(cfg: CFG, rules: tuple[str, ...] = DEFAULT_RULES,
              max_passes: int = 20) -> tuple[CFG, list[NormalizationStep]]:
    """Normalize one CFG. Returns a NEW CFG (the input is not modified) and the rule log."""
    work = CFG.from_json(cfg.to_json())
    for node in work.nodes:
        node.origin_blocks = [node.label]
    log: list[NormalizationStep] = []

    def run(rule: str) -> bool:
        if rule not in rules:
            return False
        fired = _RULE_FUNCTIONS[rule](work)
        for blocks, origin, detail in fired:
            log.append(NormalizationStep(len(log) + 1, rule, RULE_NAMES[rule], blocks.split("+"),
                                         origin, detail))
        if fired:
            _rewire(work)
        return bool(fired)

    for _ in range(max_passes):
        if not any([run(rule) for rule in _ORDER]):
            break
    for rule in _FINAL:
        run(rule)

    _relabel(work)
    work.compute_analysis()
    work.meta = dict(work.meta)
    work.meta.update({
        "normalized": True,
        "rules_applied": [r for r in ALL_RULES if r in rules],
        "rule_steps": len(log),
        "raw_blocks": len(cfg.nodes),
        "raw_edges": len(cfg.edges),
        "normalizer": GENERATOR,
        "rule_set_status": "PROPOSAL, TO CONFIRM WITH PROFESSOR [C5]",
    })
    work.analysis["loop_signature"] = loop_shapes.loop_signature(work)
    work.analysis["decision_keys"] = [cond.decision_key(work, n.id, polarity_free=True)
                                      for n in work.nodes if len(work.out_edges(n.id)) >= 2]
    work.analysis["block_signatures"] = [list(semantics.content_signature(n.operations)) for n in work.nodes]
    return work, log


def log_to_json(log: list[NormalizationStep], indent: int | None = 2) -> str:
    return json.dumps([s.to_dict() for s in log], indent=indent)
