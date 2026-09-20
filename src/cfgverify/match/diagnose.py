"""Mismatch localisation and classification.

When L1/L2 fail, produce Findings (model.evidence) that say WHERE and HOW the CFGs differ:
FUNCTION_MISSING/EXTRA, BLOCK_COUNT, EDGE_COUNT, CYCLOMATIC, LOOP_MISSING/EXTRA/NESTING, BRANCH_ARITY,
EXIT_PATHS, UNMAPPED_REGION (from the maximum common subgraph), POLARITY (L4), UNSUPPORTED_CONSTRUCT.
Status
    STUB (Part 5).
"""
