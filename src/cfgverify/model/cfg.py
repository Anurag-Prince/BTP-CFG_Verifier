"""The CFG data model shared by every part of the verifier (HLL side now, assembly side later).

One `CFG` is the control-flow graph of one function:

    Node   id (B0, B1, ...; B0 is the entry), label (the source's own name: "if.then", "7", ".LBB0_3"),
           instructions (raw text + line numbers), operations (normalized, see model/ops.py),
           predecessors, successors, terminator kind, source lines, calls,
           flags: is_entry / is_exit / loop_header, and loop_depth
    Edge   id (E0, ...), source, destination, index (successor position: 0 = TRUE target of a 2-way branch),
           kind (T / F / U / default / case:<v> / ...), back_edge, and branch information when there is a
           condition (what is tested, the normalized compare, the IR text, the C source line)
    CFG    function, origin ("hll" | "asm"), entry, exits, nodes, edges, meta (files, target, producer),
           analysis (counts, decisions, cyclomatic complexity, back edges, natural loops, unreachable blocks,
           constructs outside the supported subset)

Three interchangeable representations:
    JSON      CFG.to_dict / to_json / from_dict / from_json. Schema: model/cfg.schema.json ("cfgverify.cfg/1")
    NetworkX  CFG.to_networkx / CFG.from_networkx (a MultiDiGraph keyed by node id and edge id)
    Graphviz  model/cfg_dot.py

The graph always has exactly one node per basic block. It adds no virtual ENTRY/EXIT nodes: those belong to CFG
normalization (rule N4), which is a later stage.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import networkx as nx

from cfgverify import graph_utils
from cfgverify.model.ops import Operation

SCHEMA_ID = "cfgverify.cfg/1"
SCHEMA_PATH = Path(__file__).with_name("cfg.schema.json")


@dataclass
class InstructionRecord:
    text: str                      # the instruction exactly as in the input (comments removed)
    line: int | None               # line in the input file (.ll or .s)
    source_line: int | None = None  # C source line, when debug info is available


@dataclass
class Node:
    id: str
    label: str
    index: int
    instructions: list[InstructionRecord]
    operations: list[Operation]
    predecessors: list[str]
    successors: list[str]
    terminator: str
    source_lines: list[int] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    is_entry: bool = False
    is_exit: bool = False
    loop_header: bool = False
    loop_depth: int = 0
    origin_blocks: list[str] = field(default_factory=list)   # raw block labels this node was built from

    def to_dict(self) -> dict:
        d = asdict(self)
        d["operations"] = [o.to_dict() for o in self.operations]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        d = dict(d)
        d["instructions"] = [InstructionRecord(**i) for i in d["instructions"]]
        d["operations"] = [Operation.from_dict(o) for o in d["operations"]]
        return cls(**d)


@dataclass
class BranchInfo:
    taken_when: str                  # e.g. "v2 is true", "v2 is false", "v5 == 3", "no case matches"
    condition: str | None = None     # the tested value, canonical name ("v2") or a constant ("true")
    compare: str | None = None       # the normalized operation that computes it ("v2 = CMP.lt.s v1, 0")
    predicate: str | None = None     # LLVM predicate ("slt"), when the condition is a comparison
    ir: str | None = None            # the raw instruction that computes the condition
    case_value: str | None = None    # switch: the case constant
    source_line: int | None = None   # C line of the branch (needs -g)
    source_text: str | None = None   # that C line's text, when the C file is available
    condition_key: str | None = None        # Part 6: the comparable form, e.g. "le.s(var, var)"
    polarity_free_key: str | None = None    # Part 6, rule N6: equal for a test and its swapped negation


@dataclass
class Edge:
    id: str
    source: str
    destination: str
    index: int
    kind: str
    back_edge: bool = False
    branch: BranchInfo | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["branch"] = asdict(self.branch) if self.branch else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        d = dict(d)
        d["branch"] = BranchInfo(**d["branch"]) if d.get("branch") else None
        return cls(**d)


@dataclass
class CFG:
    function: str
    origin: str
    entry: str
    exits: list[str]
    nodes: list[Node]
    edges: list[Edge]
    meta: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)

    # ------------------------------------------------------------------------------------------------ access
    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def node_by_label(self, label: str) -> Node:
        for n in self.nodes:
            if n.label == label:
                return n
        raise KeyError(label)

    def out_edges(self, node_id: str) -> list[Edge]:
        return sorted((e for e in self.edges if e.source == node_id), key=lambda e: e.index)

    # -------------------------------------------------------------------------------------------- analysis
    def compute_analysis(self) -> "CFG":
        """Fill `analysis`, the back_edge flags on edges, and loop_header/loop_depth on nodes (graph_utils)."""
        g = self.to_networkx(include_attributes=False)
        reachable = {self.entry} | nx.descendants(g, self.entry)
        reach_g = g.subgraph(reachable)
        back = set(graph_utils.back_edges(reach_g, self.entry))
        loops = graph_utils.natural_loops(reach_g, self.entry)
        depth_of = {n: sum(1 for body in loops.values() if n in body) for n in g.nodes}
        order = {n.id: n.index for n in self.nodes}
        for e in self.edges:
            e.back_edge = (e.source, e.destination) in back
        for n in self.nodes:
            n.loop_header = n.id in loops
            n.loop_depth = depth_of.get(n.id, 0)
        header_depth = graph_utils.loop_depths(reach_g, self.entry)
        self.analysis = {
            "blocks": len(self.nodes),
            "edges": len(self.edges),
            "decisions": sum(1 for n in self.nodes if len(self.out_edges(n.id)) >= 2),
            "cyclomatic": len(self.edges) - len(self.nodes) + 2,
            "back_edges": [list(b) for b in sorted(back, key=lambda b: (order[b[0]], order[b[1]]))],
            "loops": [
                {"header": h, "depth": header_depth[h], "body": sorted(body, key=order.get)}
                for h, body in sorted(loops.items(), key=lambda kv: order[kv[0]])
            ],
            "unreachable": [n.id for n in self.nodes if n.id not in reachable],
            "unsupported": sorted({n.terminator for n in self.nodes
                                   if n.terminator not in ("br", "cond_br", "ret", "unreachable")}),
        }
        return self

    def check_invariants(self) -> list[str]:
        """Structural self-consistency. Returns a list of problems; an empty list means consistent."""
        problems = []
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            problems.append("duplicate node ids")
        idset = set(ids)
        if self.entry not in idset:
            problems.append(f"entry {self.entry} is not a node")
        if [n.index for n in self.nodes] != list(range(len(self.nodes))):
            problems.append("node indices are not 0..n-1 in order")
        for e in self.edges:
            if e.source not in idset or e.destination not in idset:
                problems.append(f"edge {e.id} references a missing node")
        for n in self.nodes:
            outs = self.out_edges(n.id)
            if [e.index for e in outs] != list(range(len(outs))):
                problems.append(f"{n.id}: successor indices are not 0..k-1")
            succ = list(dict.fromkeys(e.destination for e in outs))
            if succ != n.successors:
                problems.append(f"{n.id}: successors {n.successors} != edges {succ}")
            pred = {e.source for e in self.edges if e.destination == n.id}
            if pred != set(n.predecessors):
                problems.append(f"{n.id}: predecessors {n.predecessors} != edges {sorted(pred)}")
            if n.is_exit != (n.id in self.exits):
                problems.append(f"{n.id}: is_exit flag disagrees with exits")
            if n.is_exit and outs:
                problems.append(f"{n.id}: an exit node has successors")
            if n.terminator == "cond_br" and [e.kind for e in outs] != ["T", "F"]:
                problems.append(f"{n.id}: a 2-way branch must have edges T then F")
            if self.origin == "hll" and not self.meta.get("normalized") and n.operations and \
                    n.operations[-1].op not in (
                    "JUMP", "BRANCH", "SWITCH", "RETURN", "UNREACHABLE", "INDIRECT"):
                # In assembly a block can end simply because the next instruction is a branch target,
                # so its last instruction is an ordinary one (a fall-through block).
                problems.append(f"{n.id}: last operation is not a terminator")
        if self.node(self.entry).predecessors and self.origin == "hll" and not self.meta.get("normalized"):
            # After normalization a loop may branch back to the merged entry block, which is legitimate.
            problems.append("the LLVM entry block must not have predecessors")
        return problems

    # ------------------------------------------------------------------------------------------------ JSON
    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_ID,
            "function": self.function,
            "origin": self.origin,
            "entry": self.entry,
            "exits": list(self.exits),
            "meta": self.meta,
            "analysis": self.analysis,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CFG":
        if d.get("schema") != SCHEMA_ID:
            raise ValueError(f"not a {SCHEMA_ID} document (schema={d.get('schema')!r})")
        return cls(d["function"], d["origin"], d["entry"], list(d["exits"]),
                   [Node.from_dict(n) for n in d["nodes"]], [Edge.from_dict(e) for e in d["edges"]],
                   dict(d.get("meta", {})), dict(d.get("analysis", {})))

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "CFG":
        return cls.from_dict(json.loads(text))

    # -------------------------------------------------------------------------------------------- NetworkX
    def to_networkx(self, include_attributes: bool = True) -> nx.MultiDiGraph:
        """MultiDiGraph: nodes keyed by id, edges keyed by edge id. All fields become plain-data attributes."""
        g = nx.MultiDiGraph()
        if include_attributes:
            g.graph.update(schema=SCHEMA_ID, function=self.function, origin=self.origin, entry=self.entry,
                           exits=list(self.exits), meta=self.meta, analysis=self.analysis)
        for n in self.nodes:
            attrs = n.to_dict() if include_attributes else {}
            attrs.pop("id", None)
            g.add_node(n.id, **attrs)
        for e in self.edges:
            attrs = e.to_dict() if include_attributes else {"index": e.index, "kind": e.kind}
            for k in ("id", "source", "destination"):
                attrs.pop(k, None)
            g.add_edge(e.source, e.destination, key=e.id, **attrs)
        return g

    @classmethod
    def from_networkx(cls, g: nx.MultiDiGraph) -> "CFG":
        nodes = [Node.from_dict({"id": nid, **attrs}) for nid, attrs in g.nodes(data=True)]
        nodes.sort(key=lambda n: n.index)
        edges = [Edge.from_dict({"id": key, "source": u, "destination": v, **attrs})
                 for u, v, key, attrs in g.edges(keys=True, data=True)]
        order = {n.id: n.index for n in nodes}
        edges.sort(key=lambda e: (order[e.source], e.index))
        return cls(g.graph["function"], g.graph["origin"], g.graph["entry"], list(g.graph["exits"]),
                   nodes, edges, dict(g.graph.get("meta", {})), dict(g.graph.get("analysis", {})))


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validate_json(doc: dict) -> None:
    """Raise jsonschema.ValidationError if `doc` does not follow model/cfg.schema.json."""
    import jsonschema

    jsonschema.validate(doc, load_schema())
