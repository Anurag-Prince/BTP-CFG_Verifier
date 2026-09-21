"""Part 6: make the HLL/LLVM CFG and the assembly CFG comparable.

Four layers, each usable on its own:

    semantics.py    instruction -> semantic category (ASSIGN, ADD, COMPARE, CONDITIONAL_BRANCH, ...)
    conditions.py   branch condition -> comparable form (relation, signedness, operand shape, polarity)
    loops.py        loop nest -> comparable signature, with no block names in it
    rules.py        CFG rewriting N1-N8, applied identically to both sides, every step logged
    checks.py       proof that the rewriting kept the control-flow meaning (the Part 6 checkpoint)

Nothing in this package knows which side a CFG came from. That is the point: if normalization behaved
differently for the HLL side and the assembly side, every later comparison would be worthless.
"""

from cfgverify.normalize.rules import (ALL_RULES, DEFAULT_RULES, EXIT_ID, TRAP_ID, NormalizationStep,
                                       log_to_json, normalize)

__all__ = ["normalize", "NormalizationStep", "log_to_json", "ALL_RULES", "DEFAULT_RULES", "EXIT_ID", "TRAP_ID"]
