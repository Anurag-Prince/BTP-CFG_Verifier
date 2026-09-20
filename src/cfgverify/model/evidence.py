"""Evidence records: the only input the verdict logic and the AI explainer receive.

Responsibility
    Immutable, JSON-serialisable records of what the deterministic analysis found.
Planned types (Part 5)
    NormalizationStep   rule id (N1..N7), side, blocks affected, before/after summary.
    Finding             id, kind (FUNCTION_MISSING, LOOP_MISSING, BRANCH_ARITY, ...), function,
                        hll_blocks, asm_blocks, asm_line_range, c_lines (optional), description.
    Mapping             HLL block id -> ASM block id, per function.
    Verdict             MATCH | MISMATCH | UNSUPPORTED (UNSUPPORTED pending [C3]); created ONLY
                        by report/verdict.py.
Status
    STUB (Part 5).
"""
