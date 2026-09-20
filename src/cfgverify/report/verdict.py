"""The ONLY place where a Verdict is created (scope rule R4).

MATCH       every paired function passes L1 + L2 (+ L4 if [C1] requires it) and there are no function-level findings.
MISMATCH    at least one mismatch finding.
UNSUPPORTED input outside the approved subset (proposal; pending [C3]).
This module must never import from cfgverify.explain.
Status
    STUB (Part 5).
"""
