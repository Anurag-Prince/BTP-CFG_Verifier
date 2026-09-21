"""The optional explanation layer — rule R5: **AI never decides correctness, it only explains evidence.**

The verdict is already decided when this module is called. `report/verdict.py` produced it from the
deterministic findings, and nothing here can change it. What this layer adds is prose: a paragraph a human can
read without knowing what a dominator is.

Two producers, and the order matters:

    template()   a deterministic explanation built from the evidence record alone. No model, no network, no
                 configuration. **This is the default**, and the system is complete without any AI at all.
    model()      an optional language-model explanation, used only when a provider is configured. Its output
                 is checked before it is shown, and discarded if it fails.

The guardrails on `model()`, in full:

    1. **Input is the frozen evidence only** — the verdict, the findings, the per-function numbers. No tools,
       no re-running the analysis, no access to the CFGs it might reinterpret.
    2. **The deterministic verdict is printed first**, above the prose, and the prose is labelled
       *non-authoritative*.
    3. **Post-check**, in `check()`: the text may cite only finding ids that exist, must not name a verdict
       other than the one that was decided, and must not claim a kind of finding that was not reported. A
       failing text is discarded and the template is used instead — silently to the reader, loudly in the
       returned `Explanation.rejected`.
    4. **No provider configured is the normal case.** There is no default provider, no key in the repo, and
       no network call unless the caller passes one in.

Which provider, and whether an AI explanation is wanted at all, is **[E1]**, TO CONFIRM WITH PROFESSOR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cfgverify.model.evidence import FINDING_KINDS, VERDICTS, Verdict

#: A provider is any callable taking the prompt and returning the model's text. The project ships none.
Provider = "Callable[[str], str]"


@dataclass
class Explanation:
    text: str
    source: str                                   # "template" | "model"
    rejected: tuple[str, ...] = field(default=())  # why a model answer was discarded, if it was

    def __str__(self) -> str:
        return self.text


# ------------------------------------------------------------------------------ the deterministic one
def template(report: Verdict, source: str | Path | None = None) -> str:
    """An explanation derived from the evidence by rule. Always available, never wrong, never invented."""
    lines = ["## Explanation", ""]
    name = Path(source).name if source else "the C program"
    summary = report.summary

    if report.verdict == "MATCH":
        lines.append(
            f"The control flow of {name} and of the assembly agree. Every function was paired by name, and "
            f"for each one the two control-flow graphs were found to be the same graph: the same basic "
            f"blocks, connected the same way, with the same decisions and the same loops. The block mapping "
            f"below is the correspondence that was found.")
    elif report.verdict == "UNSUPPORTED":
        constructs = sorted({f.detail.get("construct", "?") for f in report.all_findings()
                             if f.kind == "UNSUPPORTED_CONSTRUCT"})
        lines.append(
            f"No verdict is claimed for {name}. The analysis met "
            f"{'a construct' if len(constructs) == 1 else 'constructs'} outside the supported subset "
            f"({', '.join(constructs)}), so the control-flow graph does not describe the whole program and "
            f"a MATCH or MISMATCH answer would not be honest.")
    else:
        lines.append(
            f"The control flow of {name} and of the assembly do **not** agree. "
            f"{summary.get('errors', 0)} difference(s) were found that change what the program can do.")

    lines.append("")
    for result in report.functions:
        if not result.findings and result.verdict == "MATCH":
            continue
        left, right = result.hll_summary, result.asm_summary
        lines.append(f"### `{result.function}` — {result.verdict}")
        lines.append("")
        if left and right:
            lines.append(
                f"The C side has {left.get('blocks', '?')} basic blocks, {left.get('edges', '?')} edges, "
                f"{left.get('decisions', '?')} decision(s) and {left.get('loops', '?')} loop(s); the "
                f"assembly has {right.get('blocks', '?')}, {right.get('edges', '?')}, "
                f"{right.get('decisions', '?')} and {right.get('loops', '?')}.")
            lines.append("")
        for finding in result.findings:
            lines.append(f"- **{finding.id}** ({finding.kind}): {finding.description}")
            where = []
            if finding.c_lines:
                where.append(f"C line(s) {', '.join(map(str, finding.c_lines))}")
            if finding.asm_lines:
                where.append(f"assembly line(s) {', '.join(map(str, list(finding.asm_lines)[:6]))}")
            if where:
                lines.append(f"  - Where: {'; '.join(where)}.")
            lines.append(f"  - Why it matters: {FINDING_KINDS[finding.kind][1]}.")
        lines.append("")

    lines += ["This explanation was produced from the evidence record by a fixed rule, not by a language "
              "model. Every sentence above restates a field of the record; nothing is inferred."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------- the optional one
PROMPT = """You are explaining the result of a deterministic control-flow verification to a reader who knows
C but not compiler internals.

The verdict has ALREADY been decided by a deterministic analysis. You may not change it, question it, or
suggest a different one. Explain the evidence below in plain English: what was compared, what was found, and
what each finding means for the program.

Rules you must follow:
  - State the verdict exactly as given: {verdict}.
  - Refer to findings only by the ids listed below, and only describe what their descriptions say.
  - Do not invent line numbers, block names, findings or causes.
  - If there are no findings, say that the graphs matched and why that is meaningful.
  - At most {limit} words.

EVIDENCE
{evidence}
"""


#: What a model may see of a function's summary. File paths, target triples and the machine this ran on are
#: not evidence about the program, and a model has no business being told them.
_COUNTS = ("blocks", "edges", "decisions", "cyclomatic", "loops", "max_loop_depth", "exits", "returns",
           "back_edges", "loop_signature")


def _counts(summary: dict) -> dict:
    return {key: summary[key] for key in _COUNTS if key in summary}


def build_prompt(report: Verdict, limit: int = 300) -> str:
    """Exactly what a model is given: the frozen record's numbers and findings, and nothing else.

    Deliberately excluded: file paths, the target triple, the ISA, and anything else about the machine this
    ran on. They are not evidence about the program, and leaking them into a prompt is a needless disclosure.
    """
    lines = [f"Verdict: {report.verdict}", f"Summary: {report.summary}", ""]
    for result in report.functions:
        lines.append(f"function {result.function}: {result.verdict}")
        lines.append(f"  C side: {_counts(result.hll_summary)}")
        lines.append(f"  assembly side: {_counts(result.asm_summary)}")
        for finding in result.findings:
            lines.append(f"  {finding.id} {finding.kind} ({finding.severity}): {finding.description}")
            if finding.c_lines:
                lines.append(f"    C lines: {list(finding.c_lines)}")
            if finding.asm_lines:
                lines.append(f"    assembly lines: {list(finding.asm_lines)[:8]}")
    return PROMPT.format(verdict=report.verdict, limit=limit, evidence="\n".join(lines))


def check(text: str, report: Verdict) -> list[str]:
    """The post-check. Returns the reasons the text must be rejected; empty means it may be shown."""
    problems: list[str] = []
    ids = {f.id for f in report.all_findings()}
    kinds = {f.kind for f in report.all_findings()}

    for cited in set(re.findall(r"\bF\d+\b", text)):
        if cited not in ids:
            problems.append(f"cites {cited}, which is not a finding of this run")

    for verdict in VERDICTS:
        if verdict != report.verdict and re.search(rf"\b{verdict}\b", text):
            problems.append(f"states {verdict} when the deterministic verdict is {report.verdict}")

    for kind in FINDING_KINDS:
        if kind not in kinds and re.search(rf"\b{kind}\b", text):
            problems.append(f"claims a {kind} finding, which was not reported")

    if report.verdict == "MATCH" and re.search(r"\b(mismatch|differ|inconsistent|bug)\w*\b", text, re.I):
        problems.append("describes a difference although the verdict is MATCH")
    if not text.strip():
        problems.append("empty")
    return problems


def model(report: Verdict, provider, limit: int = 300) -> Explanation:
    """Ask a provider, then check its answer. A failing answer never reaches the reader."""
    try:
        text = provider(build_prompt(report, limit=limit))
    except Exception as exc:                                  # noqa: BLE001 - a provider may do anything
        return Explanation(template(report), "template", (f"provider failed: {exc}",))
    problems = check(text, report)
    if problems:
        return Explanation(template(report), "template", tuple(problems))
    return Explanation(_frame(text, report), "model")


def _frame(text: str, report: Verdict) -> str:
    """The deterministic verdict first; the prose second, and labelled."""
    return (f"## Explanation\n\n"
            f"**Deterministic verdict: {report.verdict}** (decided by the analysis, not by the text below)\n\n"
            f"### AI explanation (non-authoritative)\n\n{text.strip()}\n")


def explain(report: Verdict, source: str | Path | None = None, provider=None,
            limit: int = 300) -> str:
    """The entry point the pipeline uses: a model explanation when one is configured and passes, else the
    deterministic template. Never raises, never blocks a verdict."""
    if provider is None:
        return template(report, source)
    return model(report, provider, limit=limit).text
