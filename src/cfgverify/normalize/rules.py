"""Normalization rules N1–N7 (docs/ARCHITECTURE.md §7.4). Approval pending: [C5].

N1 unreachable-block removal     N2 jump-only block bypass     N3 straight-line chain merge
N4 single virtual EXIT           N5 degenerate branch -> jump   N6 polarity-free conditional edges
N7 noreturn -> TRAP sink (Phase B)

Contract
    * Every rule runs on both CFGs with no knowledge of which side the CFG came from.
    * Rules run until nothing changes.
    * Every application produces a model.evidence.NormalizationStep, so a reviewer can replay it.
Status
    STUB (Part 4).
"""
