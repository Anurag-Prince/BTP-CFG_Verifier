"""Evidence records: the only input the verdict logic and the AI explainer receive.

Rule R4/R5 of the brief live here. The deterministic analysis produces immutable, JSON-serialisable records of
what it found; `report/verdict.py` turns them into a verdict; the AI explainer (Part 8) may read them and
write prose, and may not change a single field. Everything the explainer is ever allowed to say has to be
traceable to a record in this file.

    Finding             one located difference: kind, function, the blocks on each side, the assembly line
                        range, the C source lines, and a deterministic one-line description
    Mapping             normalized HLL block -> normalized assembly block, per function, with how it was found
    FunctionResult      one function's verdict, mapping, findings and the numbers behind them
    Verdict             MATCH | MISMATCH | UNSUPPORTED for the whole comparison

Nothing here computes anything. Building a record is not deciding a verdict.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# ------------------------------------------------------------------------------- the finding vocabulary
#: The ten kinds the project brief asks for, plus the four the earlier parts already need. Every kind carries
#: what it means, because a reader of a report should not have to guess.
FINDING_KINDS: dict[str, tuple[str, str]] = {
    # name                        origin      meaning
    "MISSING_BLOCK":             ("brief",     "a basic block of the HLL CFG has no counterpart in the assembly"),
    "EXTRA_BLOCK":               ("brief",     "the assembly has a basic block the HLL CFG does not"),
    "MISSING_EDGE":              ("brief",     "an HLL control-flow edge has no counterpart in the assembly"),
    "EXTRA_EDGE":                ("brief",     "the assembly has a control-flow edge the HLL CFG does not"),
    "WRONG_BRANCH":              ("brief",     "a decision maps to a block that branches differently "
                                               "(different number of arms, or the arms lead elsewhere)"),
    "WRONG_BRANCH_CONDITION":    ("brief",     "the arms line up but the condition tested is not the same"),
    "MISSING_LOOP_BACK_EDGE":    ("brief",     "a loop's back edge has no counterpart: the assembly may not loop"),
    "UNEXPECTED_PATH":           ("brief",     "the assembly can execute a sequence of operations the HLL cannot"),
    "MISSING_RETURN":            ("brief",     "a return path of the HLL CFG has no counterpart in the assembly"),
    "STRUCTURAL_MISMATCH":       ("brief",     "the graphs differ in a way none of the above describes; the "
                                               "invariant that failed is named in the description"),
    "FUNCTION_MISSING":          ("extension", "a function of the C file is not in the assembly [B4]"),
    "FUNCTION_EXTRA":            ("extension", "the assembly defines a function the C file does not [B4]"),
    "UNSUPPORTED_CONSTRUCT":     ("extension", "something outside the supported subset (indirect jump, jump "
                                               "table, inline assembly): no verdict is claimed [C3]"),
    "AMBIGUOUS_MAPPING":         ("extension", "several block mappings fit equally well; reported, never guessed"),
}
BRIEF_KINDS = tuple(k for k, (o, _) in FINDING_KINDS.items() if o == "brief")

#: How much a finding weighs. Only `error` can turn a verdict into MISMATCH; `warning` is reported and, by
#: default, does not (see report/verdict.py and **[C1]**).
SEVERITIES = ("error", "warning", "info")

VERDICTS = ("MATCH", "MISMATCH", "UNSUPPORTED")


@dataclass(frozen=True)
class Finding:
    """One located difference. Deterministic: same inputs, same findings, same order."""
    id: str
    kind: str
    severity: str
    function: str
    description: str                                  # one line, no hedging, no adjectives
    hll_blocks: tuple[str, ...] = ()                  # normalized HLL block ids
    asm_blocks: tuple[str, ...] = ()                  # normalized assembly block ids
    hll_labels: tuple[str, ...] = ()                  # the raw labels behind them (entry, while.cond, .LBB0_2)
    asm_labels: tuple[str, ...] = ()
    asm_lines: tuple[int, ...] = ()                   # lines in the .s file, when known
    c_lines: tuple[int, ...] = ()                     # C source lines, when -g gave them
    detail: dict = field(default_factory=dict)        # kind-specific numbers, never prose

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        d = dict(d)
        for key in ("hll_blocks", "asm_blocks", "hll_labels", "asm_labels", "asm_lines", "c_lines"):
            d[key] = tuple(d.get(key, ()))
        return cls(**d)

    def __str__(self) -> str:
        where = ""
        if self.hll_labels or self.asm_labels:
            where = f"  [{'+'.join(self.hll_labels) or '-'} | {'+'.join(self.asm_labels) or '-'}]"
        return f"{self.severity.upper():<8}{self.kind:<24}{self.description}{where}"


@dataclass(frozen=True)
class Mapping:
    """Which normalized HLL block corresponds to which normalized assembly block."""
    function: str
    pairs: tuple[tuple[str, str], ...]                # (hll block id, asm block id)
    method: str                                       # "isomorphism" | "refinement" | "none"
    complete: bool                                    # every block on both sides is paired
    hll_unmapped: tuple[str, ...] = ()
    asm_unmapped: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pairs"] = [list(p) for p in self.pairs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Mapping":
        d = dict(d)
        d["pairs"] = tuple(tuple(p) for p in d.get("pairs", ()))
        for key in ("hll_unmapped", "asm_unmapped"):
            d[key] = tuple(d.get(key, ()))
        return cls(**d)

    def of(self, hll_block: str) -> str | None:
        return dict(self.pairs).get(hll_block)


@dataclass(frozen=True)
class FunctionResult:
    """Everything the deterministic analysis concluded about one function."""
    function: str
    verdict: str
    findings: tuple[Finding, ...]
    mapping: Mapping | None
    hll_summary: dict = field(default_factory=dict)   # blocks, edges, decisions, loops, exits, signature
    asm_summary: dict = field(default_factory=dict)
    levels: dict = field(default_factory=dict)        # which matcher level said what (L1/L2/L3/L4)

    def to_dict(self) -> dict:
        return {"function": self.function, "verdict": self.verdict,
                "findings": [f.to_dict() for f in self.findings],
                "mapping": self.mapping.to_dict() if self.mapping else None,
                "hll_summary": self.hll_summary, "asm_summary": self.asm_summary, "levels": self.levels}

    @classmethod
    def from_dict(cls, d: dict) -> "FunctionResult":
        return cls(d["function"], d["verdict"], tuple(Finding.from_dict(f) for f in d["findings"]),
                   Mapping.from_dict(d["mapping"]) if d.get("mapping") else None,
                   dict(d.get("hll_summary", {})), dict(d.get("asm_summary", {})), dict(d.get("levels", {})))

    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")


@dataclass(frozen=True)
class Verdict:
    """The answer for the whole comparison. Created ONLY by report/verdict.py."""
    verdict: str
    functions: tuple[FunctionResult, ...]
    summary: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"schema": "cfgverify.verdict/1", "verdict": self.verdict,
                "functions": [f.to_dict() for f in self.functions],
                "summary": self.summary, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "Verdict":
        if d.get("schema") != "cfgverify.verdict/1":
            raise ValueError(f"not a cfgverify.verdict/1 document (schema={d.get('schema')!r})")
        return cls(d["verdict"], tuple(FunctionResult.from_dict(f) for f in d["functions"]),
                   dict(d.get("summary", {})), dict(d.get("meta", {})))

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "Verdict":
        return cls.from_dict(json.loads(text))

    def all_findings(self) -> tuple[Finding, ...]:
        return tuple(f for result in self.functions for f in result.findings)


def new_finding(counter: list[int], kind: str, severity: str, function: str, description: str,
                **kwargs) -> Finding:
    """Allocate the next finding id. `counter` is a one-element list so the caller keeps the count."""
    if kind not in FINDING_KINDS:
        raise ValueError(f"unknown finding kind {kind!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    counter[0] += 1
    return Finding(id=f"F{counter[0]}", kind=kind, severity=severity, function=function,
                   description=description, **kwargs)
