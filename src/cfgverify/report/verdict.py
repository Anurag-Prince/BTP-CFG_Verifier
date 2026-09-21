"""**The only place a verdict is created.** Rule R4 of the brief.

Everything upstream produces facts: CFGs, a mapping, findings. This module applies one small, stated policy to
those facts and returns a `Verdict`. Nothing else in the project constructs one, and the AI explainer (Part 8)
receives the finished record and cannot change it.

The policy, in full:

    UNSUPPORTED   the comparison ran into something outside the supported subset, so no honest answer is
                  possible: an indirect jump or jump table, or a function present on one side only.
                  (Allowing this at all is **[C3]**; if the professor wants strict MATCH/MISMATCH, set
                  `allow_unsupported=False` and those become MISMATCH.)
    MISMATCH      at least one finding of severity `error`.
    MATCH         no finding of severity `error`, and the two graphs were isomorphic with a complete mapping.

Severity, not kind, decides. A `WRONG_BRANCH_CONDITION` is a `warning` by default, because what "consistent
control-flow semantics" means is **[C1]**: at level (a)–(c) the structure is what counts, and compilers
legitimately rewrite a test (`x > 1` emitted as `x >= 2`). `require_conditions=True` raises those findings to
errors, which is the (d) reading of [C1]. The choice is a parameter, never a silent default.

A verdict is never reached by accident: `MATCH` requires a *positive* result from the matcher (an isomorphism
with every block mapped), not merely the absence of complaints.
"""

from __future__ import annotations

from dataclasses import replace

from cfgverify import __version__
from cfgverify.model.evidence import FunctionResult, Mapping, Verdict

GENERATOR = f"cfgverify {__version__} report.verdict"


def decide_function(function: str, findings, result, mapping: Mapping | None, hll_summary: dict,
                    asm_summary: dict, *, require_conditions: bool = False,
                    allow_unsupported: bool = True) -> FunctionResult:
    """The verdict for one function, from its findings and what the matcher concluded."""
    findings = tuple(_apply_condition_policy(findings, require_conditions))
    errors = [f for f in findings if f.severity == "error"]
    unsupported = [f for f in findings if f.kind == "UNSUPPORTED_CONSTRUCT"]

    if unsupported and allow_unsupported:
        verdict = "UNSUPPORTED"
    elif errors:
        verdict = "MISMATCH"
    elif result is not None and result.isomorphic and mapping is not None and mapping.complete:
        verdict = "MATCH"
    else:
        verdict = "MISMATCH"
    return FunctionResult(function=function, verdict=verdict, findings=findings, mapping=mapping,
                          hll_summary=hll_summary, asm_summary=asm_summary,
                          levels=dict(result.levels) if result is not None else {})


def decide(results: list[FunctionResult], meta: dict | None = None,
           *, allow_unsupported: bool = True) -> Verdict:
    """The verdict for the whole comparison: the worst of its functions."""
    verdicts = [r.verdict for r in results]
    if not results:
        overall = "UNSUPPORTED" if allow_unsupported else "MISMATCH"
    elif "MISMATCH" in verdicts:
        overall = "MISMATCH"
    elif "UNSUPPORTED" in verdicts:
        overall = "UNSUPPORTED" if allow_unsupported else "MISMATCH"
    else:
        overall = "MATCH"

    findings = [f for r in results for f in r.findings]
    by_kind: dict[str, int] = {}
    for finding in findings:
        by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
    summary = {
        "functions": len(results),
        "matched": verdicts.count("MATCH"),
        "mismatched": verdicts.count("MISMATCH"),
        "unsupported": verdicts.count("UNSUPPORTED"),
        "findings": len(findings),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "by_kind": dict(sorted(by_kind.items())),
    }
    return Verdict(verdict=overall, functions=tuple(results), summary=summary,
                   meta={"generator": GENERATOR, **(meta or {})})


def _apply_condition_policy(findings, require_conditions: bool):
    """`WRONG_BRANCH_CONDITION` is a warning unless the caller asked for the strict reading of [C1]."""
    for finding in findings:
        if finding.kind == "WRONG_BRANCH_CONDITION" and require_conditions:
            yield replace(finding, severity="error")
        else:
            yield finding
