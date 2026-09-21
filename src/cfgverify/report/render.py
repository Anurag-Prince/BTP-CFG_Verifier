"""Render a `Verdict` for a human: a terminal summary and a Markdown report.

Everything printed here comes from the evidence record. Nothing is inferred, softened or summarised into a
judgement — the verdict was already decided in `report/verdict.py`, and this module only lays it out. That
separation is what lets Part 8's AI explanation be checked: the explainer sees exactly this material.
"""

from __future__ import annotations

from pathlib import Path

from cfgverify.model.evidence import FINDING_KINDS, Verdict

_MARK = {"MATCH": "MATCH", "MISMATCH": "MISMATCH", "UNSUPPORTED": "UNSUPPORTED"}


def terminal(report: Verdict, *, show_findings: bool = True, show_mapping: bool = False) -> str:
    """One table of functions, then the findings. Written to be readable in a checkpoint log."""
    lines = []
    lines.append(f"{'function':<24}{'verdict':<13}{'blocks':>14}{'edges':>13}{'dec':>9}{'loops':>9}"
                 f"  findings")
    for result in report.functions:
        left, right = result.hll_summary, result.asm_summary
        pair = lambda key: f"{left.get(key, '-')} vs {right.get(key, '-')}"          # noqa: E731
        errors = sum(1 for f in result.findings if f.severity == "error")
        warnings = sum(1 for f in result.findings if f.severity == "warning")
        note = ", ".join(x for x in (f"{errors} error(s)" if errors else "",
                                     f"{warnings} warning(s)" if warnings else "") if x) or "-"
        lines.append(f"{result.function[:23]:<24}{_MARK.get(result.verdict, result.verdict):<13}"
                     f"{pair('blocks'):>14}{pair('edges'):>13}{pair('decisions'):>9}{pair('loops'):>9}  {note}")
    if show_findings:
        for result in report.functions:
            if not result.findings:
                continue
            lines.append(f"\n  {result.function}:")
            for finding in result.findings:
                lines.append(f"    {finding.id:<5}{finding}")
                if finding.c_lines:
                    lines.append(f"          C line(s): {', '.join(map(str, finding.c_lines))}")
                if finding.asm_lines:
                    shown = list(finding.asm_lines)[:6]
                    more = "" if len(finding.asm_lines) <= 6 else f", +{len(finding.asm_lines) - 6} more"
                    lines.append(f"          assembly line(s): {', '.join(map(str, shown))}{more}")
    if show_mapping:
        for result in report.functions:
            if result.mapping and result.mapping.pairs:
                pairs = ", ".join(f"{a}->{b}" for a, b in result.mapping.pairs)
                lines.append(f"\n  {result.function} mapping ({result.mapping.method}): {pairs}")
    s = report.summary
    lines.append(f"\nVERDICT: {report.verdict}  "
                 f"({s.get('matched', 0)} match, {s.get('mismatched', 0)} mismatch, "
                 f"{s.get('unsupported', 0)} unsupported; "
                 f"{s.get('errors', 0)} error(s), {s.get('warnings', 0)} warning(s))")
    return "\n".join(lines)


def markdown(report: Verdict, title: str = "CFG consistency report") -> str:
    """A report a reader can hand in: the verdict, the numbers, every finding, and how it was reached."""
    s = report.summary
    out = [f"# {title}", "",
           f"**Verdict: {report.verdict}**", "",
           f"{s.get('functions', 0)} function(s): {s.get('matched', 0)} MATCH, "
           f"{s.get('mismatched', 0)} MISMATCH, {s.get('unsupported', 0)} UNSUPPORTED. "
           f"{s.get('errors', 0)} error(s), {s.get('warnings', 0)} warning(s).", ""]

    meta = report.meta
    if meta:
        out += ["| setting | value |", "|---|---|"]
        for key in sorted(meta):
            out.append(f"| {key} | {meta[key]} |")
        out.append("")

    out += ["## Functions", "",
            "| function | verdict | blocks (C \\| asm) | edges | decisions | loops | mapping |",
            "|---|---|---|---|---|---|---|"]
    for result in report.functions:
        left, right = result.hll_summary, result.asm_summary
        pair = lambda key: f"{left.get(key, '-')} \\| {right.get(key, '-')}"          # noqa: E731
        method = result.mapping.method if result.mapping else "none"
        out.append(f"| `{result.function}` | **{result.verdict}** | {pair('blocks')} | {pair('edges')} | "
                   f"{pair('decisions')} | {pair('loops')} | {method} |")
    out.append("")

    findings = report.all_findings()
    if findings:
        out += ["## Findings", ""]
        for result in report.functions:
            if not result.findings:
                continue
            out += [f"### `{result.function}`", ""]
            for finding in result.findings:
                out.append(f"- **{finding.id} {finding.kind}** ({finding.severity}) — {finding.description}")
                where = []
                if finding.hll_labels:
                    where.append(f"C/IR block(s): `{'`, `'.join(finding.hll_labels)}`")
                if finding.asm_labels:
                    where.append(f"assembly block(s): `{'`, `'.join(finding.asm_labels)}`")
                if finding.c_lines:
                    where.append(f"C line(s): {', '.join(map(str, finding.c_lines))}")
                if finding.asm_lines:
                    where.append(f"assembly line(s): {', '.join(map(str, list(finding.asm_lines)[:8]))}")
                if where:
                    out.append(f"  - {'; '.join(where)}")
            out.append("")
    else:
        out += ["## Findings", "", "None. Every block, edge, decision, loop and return path lined up.", ""]

    out += ["## How this was decided", "",
            "The verdict comes only from the deterministic analysis: the CFGs are built from LLVM IR and from",
            "the assembly text, normalized with the same rules on both sides, and compared by graph",
            "isomorphism over block roles and semantic content. A finding of severity `error` makes a",
            "function MISMATCH; MATCH additionally requires a complete block mapping, so it can never be",
            "reached by an absence of complaints alone.", ""]
    for result in report.functions:
        if result.levels:
            out.append(f"- `{result.function}`: " + "; ".join(f"{k} {v}" for k, v in sorted(result.levels.items())))
    out.append("")
    out += ["## Finding kinds", "",
            "| kind | meaning |", "|---|---|"]
    used = {f.kind for f in findings}
    for kind in sorted(used) or sorted(FINDING_KINDS):
        out.append(f"| `{kind}` | {FINDING_KINDS[kind][1]} |")
    return "\n".join(out) + "\n"


def write(report: Verdict, out_dir: str | Path, stem: str = "report") -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": out_dir / f"{stem}.verdict.json", "markdown": out_dir / f"{stem}.md"}
    paths["json"].write_text(report.to_json())
    paths["markdown"].write_text(markdown(report))
    return paths
