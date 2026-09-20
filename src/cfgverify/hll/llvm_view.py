"""LLVM's own view of each function's CFG, exported with `opt -passes=dot-cfg`.

Part 1 uses this as the smoke test for graph representation (networkx) and visualization (Graphviz).
From Part 2 on, it is the reference oracle: the CFG built by hll/cfg_builder.py must equal this graph.

This module is NOT the project's HLL CFG builder. It only reads what LLVM exports.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import networkx as nx
import pydot

from cfgverify.toolchain import ToolchainError, require_tool, run

_BLOCK_NAME_RE = re.compile(r'^"?\{\s*([^:|\\]+?):')


def export_dot(ll_path: str | Path, out_dir: str | Path) -> dict[str, Path]:
    """Run opt's dot-cfg pass. Returns {function_name: path/to/<function>.dot}."""
    opt = require_tool("opt")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "cfg"
        proc = run([opt, "-passes=dot-cfg", f"-cfg-dot-filename-prefix={prefix}", "-disable-output", str(ll_path)])
        if proc.returncode != 0:
            raise ToolchainError(f"opt dot-cfg failed:\n{proc.stderr.strip()}")
        result: dict[str, Path] = {}
        for dot_file in sorted(Path(tmp).glob("cfg.*.dot")):
            function = dot_file.name[len("cfg.") : -len(".dot")]
            target = out_dir / f"{function}.dot"
            target.write_text(dot_file.read_text())
            result[function] = target
    return result


def load_dot(dot_path: str | Path) -> nx.MultiDiGraph:
    """Load one LLVM dot-cfg file into networkx, with nodes named after IR basic blocks.

    LLVM draws each block as a Graphviz "record" whose successor slots are ports (Node0x..:s0). Passing
    those names to networkx unchanged would create fake extra nodes, so the ports are removed here and kept
    as an edge attribute `succ_index` (for a conditional br: 0 = true target, 1 = false target).
    """
    (graph,) = pydot.graph_from_dot_file(str(dot_path))
    names: dict[str, str] = {}
    for node in graph.get_nodes():
        node_id = node.get_name()
        if node_id in ("node", "edge", "graph"):
            continue
        label = node.get_attributes().get("label", "")
        m = _BLOCK_NAME_RE.match(label)
        names[node_id] = m.group(1).strip() if m else node_id

    g = nx.MultiDiGraph(function=Path(dot_path).stem)
    for node_id, block in names.items():
        g.add_node(block)
    for edge in graph.get_edges():
        src, _, port = edge.get_source().partition(":")
        dst = edge.get_destination().partition(":")[0]
        succ_index = int(port[1:]) if port.startswith("s") and port[1:].isdigit() else 0
        g.add_edge(names.get(src, src), names.get(dst, dst), succ_index=succ_index)
    return g


def summarize(g: nx.MultiDiGraph) -> dict:
    """Basic structural numbers. Cyclomatic complexity = E - N + 2 (single connected CFG)."""
    n, e = g.number_of_nodes(), g.number_of_edges()
    return {
        "blocks": n,
        "edges": e,
        "cyclomatic": e - n + 2,
        "two_way_branches": sum(1 for b in g if g.out_degree(b) == 2),
        "exit_blocks": sum(1 for b in g if g.out_degree(b) == 0),
    }


def render_svg(dot_path: str | Path, svg_path: str | Path | None = None) -> Path:
    """Render a DOT file to SVG with the Graphviz `dot` binary."""
    dot = require_tool("dot")
    svg_path = Path(svg_path) if svg_path else Path(dot_path).with_suffix(".svg")
    proc = run([dot, "-Tsvg", str(dot_path), "-o", str(svg_path)])
    if proc.returncode != 0:
        raise ToolchainError(f"dot failed:\n{proc.stderr.strip()}")
    return svg_path
