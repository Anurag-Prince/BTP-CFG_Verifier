"""Graphviz visualization of a model.cfg.CFG (the HLL side now, the assembly side later), plus an HTML gallery.

    to_dot(cfg, detail="ops")         -> graphviz.Digraph   (detail: "ops" | "ir" | "none")
    render(cfg, path, detail="ops")   -> writes .svg / .png / .pdf / .dot (chosen by the file suffix)
    write_gallery(entries, html_path) -> one HTML page that shows many rendered CFGs

Legend: thick border = entry; double border = exit; yellow = 2-way decision; blue = loop header;
green / red edge = T / F; grey = unconditional; dashed blue = back edge.
"""

from __future__ import annotations

import html
from pathlib import Path

from cfgverify.model.cfg import CFG

_EDGE_COLOR = {"T": "#15803d", "F": "#b91c1c", "U": "#475569"}
_BACK = "#1d4ed8"
_MAX_LINE = 72


def _esc(text: str) -> str:
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return text if len(text) <= _MAX_LINE else text[: _MAX_LINE - 1] + "…"


def _line_range(lines: list[int]) -> str:
    if not lines:
        return ""
    return f"C {lines[0]}" if len(lines) == 1 else f"C {lines[0]}–{lines[-1]}"


def to_dot(cfg: CFG, detail: str = "ops"):
    import graphviz

    a = cfg.analysis
    title = (f"{cfg.function}: {cfg.origin.upper()} CFG · {a.get('blocks')} blocks · {a.get('edges')} edges · "
             f"cyclomatic {a.get('cyclomatic')}")
    dot = graphviz.Digraph(cfg.function)
    dot.attr(label=title, labelloc="t", fontname="Helvetica", fontsize="12", nodesep="0.3", ranksep="0.35")
    dot.attr("node", shape="box", style="rounded,filled", fillcolor="white", color="#475569",
             fontname="Courier", fontsize="9")
    dot.attr("edge", fontname="Helvetica", fontsize="9", arrowsize="0.7")
    decisions = {n.id for n in cfg.nodes if len(cfg.out_edges(n.id)) >= 2}
    for n in cfg.nodes:
        header = f"{n.id}  %{n.label}" if cfg.origin == "hll" else f"{n.id}  {n.label}"
        src = _line_range(n.source_lines)
        lines = [header + (f"   [{src}]" if src else "")]
        if detail == "ops":
            lines += [o.text for o in n.operations]
        elif detail == "ir":
            lines += [i.text.split(", !dbg")[0] for i in n.instructions]
        label = "\\l".join(_esc(x) for x in lines) + "\\l"
        attrs = {}
        if n.loop_header:
            attrs["fillcolor"] = "#dbeafe"
        elif n.id in decisions:
            attrs["fillcolor"] = "#fef9c3"
        if n.is_entry:
            attrs["penwidth"] = "2.2"
        if n.is_exit:
            attrs["peripheries"] = "2"
        dot.node(n.id, label, **attrs)
    for e in cfg.edges:
        kind = e.kind if e.kind in _EDGE_COLOR else "U"
        text = "" if e.kind == "U" else e.kind
        if e.back_edge:
            text = f"{text} back".strip()
        color = _BACK if e.back_edge else _EDGE_COLOR[kind]
        tooltip = e.branch.taken_when if e.branch else "always"
        dot.edge(e.source, e.destination, label=f" {text} " if text else "", color=color, fontcolor=color,
                 style="dashed" if e.back_edge else "solid", constraint="false" if e.back_edge else "true",
                 tooltip=tooltip)
    return dot


def render(cfg: CFG, path: str | Path, detail: str = "ops") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    dot = to_dot(cfg, detail)
    fmt = path.suffix.lstrip(".") or "svg"
    if fmt in ("dot", "gv"):
        path.write_text(dot.source)
    else:
        path.write_bytes(dot.pipe(format=fmt))
    return path


def write_gallery(entries: list[dict], html_path: str | Path, title: str = "HLL CFGs") -> Path:
    """entries: dicts with keys program, function, svg, json (paths relative to the HTML file), summary."""
    html_path = Path(html_path)
    cards = []
    for e in entries:
        cards.append(
            f'<section><h2>{html.escape(e["program"])} · <code>{html.escape(e["function"])}</code></h2>'
            f'<p>{html.escape(e["summary"])} · <a href="{html.escape(e["json"])}">JSON</a> · '
            f'<a href="{html.escape(e["svg"])}">SVG</a></p>'
            f'<img src="{html.escape(e["svg"])}" alt="CFG of {html.escape(e["function"])}"></section>'
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fafafa; color: #1f2937; }}
section {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; margin: 12px 0; }}
h2 {{ font-size: 16px; margin: 0 0 4px; }} p {{ margin: 0 0 8px; font-size: 13px; color: #4b5563; }}
img {{ max-width: 100%; height: auto; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>{len(entries)} function(s). Legend: thick border = entry, double border = exit, yellow = decision,
blue = loop header, green/red = T/F edge, dashed blue = back edge.</p>
{''.join(cards)}
</body></html>
"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(page)
    return html_path
