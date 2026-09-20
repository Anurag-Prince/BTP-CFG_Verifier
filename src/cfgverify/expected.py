"""Expected CFGs of the regression programs: load them, turn them into graphs, draw them and document them.

Each regression program regression/c/<name>.c has a hand-written specification regression/expected/<name>.toml.
The spec states, for every function, the expected basic blocks, edges (T/F/U), branch conditions, back edges and
loops. tests/test_regression_cfgs.py checks each spec against LLVM's own CFG, so the documentation cannot drift
away from what Clang actually produces.

Spec format (one [[function]] entry per C function):

    program   = "if.c"
    construct = "if"

    [[function]]
    name       = "clamp_negative"
    decisions  = 1                      # number of 2-way branch blocks
    exits      = ["if.end"]             # blocks that end in `ret`
    back_edges = []                     # [[latch, header], ...]
    loops      = []                     # [{ header = "...", depth = 1, body = ["...", ...] }]
    edges      = [                      # [from, to, kind] or [from, to, kind, note]; kind: T | F | U
      ["entry", "if.then", "T"],
      ...
    ]

    [[function.block]]                  # one entry per basic block, in IR order
    name  = "entry"
    lines = [9, 10]                     # source lines whose statements execute in this block
    code  = "int y = x;  if (x < 0)"
    cond  = "x < 0"                     # only for 2-way blocks: the C condition ...
    ir    = "icmp slt"                  # ... and the LLVM instruction that computes it
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import networkx as nx

from cfgverify import graph_utils

REGRESSION_DIR = Path("regression")
# Order in which the constructs are studied (docs/CONTROL_FLOW_FUNDAMENTALS.md). Unlisted programs sort last.
STUDY_ORDER = ["sequence.c", "if.c", "if_else.c", "nested_if.c", "while.c", "for.c", "do_while.c",
               "nested_loop.c", "break.c", "continue.c"]
EDGE_KINDS = {"T": "true", "F": "false", "U": "unconditional"}


@dataclass
class ExpectedBlock:
    name: str
    lines: list[int] = field(default_factory=list)
    code: str = ""
    cond: str | None = None
    ir: str | None = None


@dataclass
class ExpectedEdge:
    src: str
    dst: str
    kind: str  # T | F | U
    note: str = ""


@dataclass
class ExpectedFunction:
    program: str
    name: str
    decisions: int
    exits: list[str]
    back_edges: list[tuple[str, str]]
    loops: list[dict]
    edges: list[ExpectedEdge]
    blocks: list[ExpectedBlock]

    @property
    def entry(self) -> str:
        return self.blocks[0].name

    @property
    def key(self) -> str:
        return f"{self.program}/{self.name}"

    def block(self, name: str) -> ExpectedBlock:
        return next(b for b in self.blocks if b.name == name)

    def graph(self) -> nx.MultiDiGraph:
        g = nx.MultiDiGraph(function=self.name)
        for b in self.blocks:
            g.add_node(b.name)
        for e in self.edges:
            g.add_edge(e.src, e.dst, kind=e.kind)
        return g


@dataclass
class ExpectedProgram:
    path: Path
    program: str
    construct: str
    functions: list[ExpectedFunction]

    @property
    def source(self) -> Path:
        return self.path.parent.parent / "c" / self.program


def load_spec(path: str | Path) -> ExpectedProgram:
    path = Path(path)
    data = tomllib.loads(path.read_text())
    functions = []
    for f in data["function"]:
        functions.append(
            ExpectedFunction(
                program=data["program"],
                name=f["name"],
                decisions=f["decisions"],
                exits=list(f["exits"]),
                back_edges=[tuple(e) for e in f.get("back_edges", [])],
                loops=list(f.get("loops", [])),
                edges=[ExpectedEdge(*e) for e in f["edges"]],
                blocks=[ExpectedBlock(**b) for b in f["block"]],
            )
        )
    return ExpectedProgram(path=path, program=data["program"], construct=data["construct"], functions=functions)


def load_all(regression_dir: str | Path = REGRESSION_DIR) -> list[ExpectedProgram]:
    """All specs, in study order (then alphabetically)."""
    programs = [load_spec(p) for p in Path(regression_dir, "expected").glob("*.toml")]
    rank = {name: i for i, name in enumerate(STUDY_ORDER)}
    return sorted(programs, key=lambda p: (rank.get(p.program, len(rank)), p.program))


# ----------------------------------------------------------------------------------------------- drawing

_COLORS = {"T": "#15803d", "F": "#b91c1c", "U": "#374151", "back": "#1d4ed8"}


def _line_range(lines: list[int]) -> str:
    if not lines:
        return "—"
    return str(lines[0]) if len(lines) == 1 else f"{min(lines)}–{max(lines)}"


def render_svg(fn: ExpectedFunction, out: str | Path) -> Path:
    """Draw the expected CFG with Graphviz.

    Blue fill = loop header, yellow fill = 2-way branch, thick border = entry, double border = exit (return).
    Green edge = T, red = F, grey = U, dashed blue = back edge.
    """
    import graphviz

    headers = {h for _, h in fn.back_edges}
    back = set(fn.back_edges)
    dot = graphviz.Digraph(fn.name)
    dot.attr(
        label=f"{fn.program} · {fn.name}  (expected CFG, Clang -O0 block names)",
        labelloc="t", fontname="Helvetica", fontsize="12", nodesep="0.35", ranksep="0.4",
    )
    dot.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="10",
             fillcolor="white", color="#475569")
    dot.attr("edge", fontname="Helvetica", fontsize="9", arrowsize="0.7")
    for b in fn.blocks:
        where = f"L{_line_range(b.lines)}: " if b.lines else ""
        label = f"{b.name}\\n{where}{b.code}" if b.code else b.name
        attrs = {}
        if b.name in headers:
            attrs["fillcolor"] = "#dbeafe"
        elif b.cond:
            attrs["fillcolor"] = "#fef9c3"
        if b.name == fn.entry:
            attrs["penwidth"] = "2.2"
        if b.name in fn.exits:
            attrs["peripheries"] = "2"
        dot.node(b.name, label, **attrs)
    for e in fn.edges:
        is_back = (e.src, e.dst) in back
        text = e.kind if e.kind != "U" else ""
        if e.note:
            text = f"{text} {e.note}".strip()
        if is_back:
            text = f"{text} (back)".strip()
        dot.edge(
            e.src, e.dst, label=f" {text} " if text else "",
            color=_COLORS["back"] if is_back else _COLORS[e.kind],
            fontcolor=_COLORS["back"] if is_back else _COLORS[e.kind],
            style="dashed" if is_back else "solid",
            constraint="false" if is_back else "true",
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(dot.pipe(format="svg"))
    return out


def svg_name(fn: ExpectedFunction) -> str:
    return f"{Path(fn.program).stem}.{fn.name}.svg"


# ------------------------------------------------------------------------------------------ documentation

def function_source(c_file: Path, name: str) -> tuple[int, list[str]]:
    """(first line number, source lines) of a top-level C function (from its header to the closing brace)."""
    lines = Path(c_file).read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^\w[\w\s\*]*\b{re.escape(name)}\(", ln))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return start + 1, lines[start : end + 1]


def _leaves_by(fn: ExpectedFunction, b: ExpectedBlock) -> str:
    if b.cond:
        return f"2-way branch on `{b.cond}` (`{b.ir}`)"
    if b.name in fn.exits:
        return "return"
    return "jump"


def _taken_when(fn: ExpectedFunction, e: ExpectedEdge) -> str:
    cond = fn.block(e.src).cond
    text = {"T": f"`{cond}` is true", "F": f"`{cond}` is false", "U": "always"}[e.kind]
    if e.note:
        text += f": **{e.note}**"
    return text


def markdown_section(prog: ExpectedProgram, fn: ExpectedFunction, svg_dir_rel: str = "cfg") -> str:
    first, src = function_source(prog.source, fn.name)
    width = len(str(first + len(src)))
    listing = "\n".join(f"{first + i:>{width}}  {ln}" for i, ln in enumerate(src))
    back = set(fn.back_edges)
    g = fn.graph()
    out = [
        f"**`{fn.name}`**: `regression/c/{prog.program}`, lines {first}–{first + len(src) - 1}",
        "",
        "```c",
        listing,
        "```",
        "",
        f"![Expected CFG of {fn.name}]({svg_dir_rel}/{svg_name(fn)})",
        "",
        "| Block | Lines | What runs there | Leaves by |",
        "|---|---|---|---|",
    ]
    for b in fn.blocks:
        code = f"`{b.code}`" if b.code else ""
        out.append(f"| `{b.name}` | {_line_range(b.lines)} | {code} | {_leaves_by(fn, b)} |")
    if fn.edges:
        out += ["", "| Edge | Kind | Taken when |", "|---|---|---|"]
    else:
        out += ["", "_No edges: the whole function is one basic block._"]
    for e in fn.edges:
        kind = e.kind + (", **back edge**" if (e.src, e.dst) in back else "")
        out.append(f"| `{e.src} → {e.dst}` | {kind} | {_taken_when(fn, e)} |")
    backs = ", ".join(f"`{t} → {h}`" for t, h in fn.back_edges) or "none"
    loops = "; ".join(
        f"header `{lp['header']}` (depth {lp['depth']}) = {{{', '.join(lp['body'])}}}" for lp in fn.loops
    ) or "none"
    out += [
        "",
        f"Blocks **{g.number_of_nodes()}** · edges **{g.number_of_edges()}** · decisions **{fn.decisions}** · "
        f"cyclomatic **{graph_utils.cyclomatic(g)}** · back edges: {backs} · loops: {loops}",
    ]
    return "\n".join(out)


def summary_table(programs: list[ExpectedProgram]) -> str:
    rows = [
        "| Program | Function | Blocks | Edges | Decisions | Cyclomatic | Back edges | Loops (header: depth) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for prog in programs:
        for fn in prog.functions:
            g = fn.graph()
            loops = ", ".join(f"`{lp['header']}`: {lp['depth']}" for lp in fn.loops) or "—"
            rows.append(
                f"| `{prog.program}` | `{fn.name}` | {g.number_of_nodes()} | {g.number_of_edges()} | {fn.decisions} | "
                f"{graph_utils.cyclomatic(g)} | {len(fn.back_edges)} | {loops} |"
            )
    return "\n".join(rows)


_MARKER_RE = re.compile(
    r"(<!-- BEGIN GENERATED cfg: (?P<key>[\w.]+/\w+) -->\n)(?P<body>.*?)(<!-- END GENERATED cfg: (?P=key) -->)",
    re.DOTALL,
)


def refresh_doc(doc_text: str, programs: list[ExpectedProgram], svg_dir_rel: str = "cfg") -> str:
    """Replace every generated region of the study document with fresh content from the specs."""
    by_key = {fn.key: (prog, fn) for prog in programs for fn in prog.functions}

    def repl(m: re.Match) -> str:
        key = m.group("key")
        if key == "summary/all":
            return f"{m.group(1)}{summary_table(programs)}\n{m.group(4)}"
        if key not in by_key:
            raise KeyError(f"document refers to unknown function spec {key}")
        prog, fn = by_key[key]
        return f"{m.group(1)}{markdown_section(prog, fn, svg_dir_rel)}\n{m.group(4)}"

    return _MARKER_RE.sub(repl, doc_text)
