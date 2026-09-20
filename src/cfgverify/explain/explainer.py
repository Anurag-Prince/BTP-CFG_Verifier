"""AI explanation of deterministic evidence (docs/ARCHITECTURE.md §7.8).

Guardrails
    1. Input: only the frozen evidence JSON plus source/assembly excerpts. No tools, and no re-running analysis.
    2. The deterministic verdict is printed first. AI text goes under "AI explanation (non-authoritative)".
    3. Post-check: the output may cite only existing finding IDs and must not state a different verdict;
       otherwise it is discarded and a deterministic template explanation is used instead.
    4. The whole system works offline without this module.
Provider/model: TO CONFIRM WITH PROFESSOR ([E1]).
Status
    STUB (Part 7).
"""
