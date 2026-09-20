"""Orchestrates pipeline stages 1–9 (docs/ARCHITECTURE.md §5).

Responsibility
    Wire the stages together for one (C file, assembly file) pair:
    frontend -> ir_reader -> hll.cfg_builder | asm.reader -> asm.cfg_builder
    -> normalize (both) -> match -> report.verdict -> (optional) explain.
Input
    Path to .c, path to assembly, a run configuration (target triple, ISA plug-in, output dir).
Output
    A verdict record and an evidence bundle written to the output directory.
Status
    STUB. Implemented after Parts 2–5. Contains no verification logic by design: it only calls modules.
"""
