"""LLVM IR -> functions -> basic blocks -> terminators.

Responsibility
    Read a .ll file and list, for each defined function, its basic blocks in order and each block's
    terminator with its successor labels.
Design notes (docs/ARCHITECTURE.md §7.2)
    * Library: llvmlite (llvmlite.binding.parse_assembly).
    * PITFALL found while probing: for a conditional `br i1 %c, label %T, label %F`, llvmlite lists the label
      operands in LLVM's internal order [cond, %F, %T]. Read the documented positions and never rely on the
      order they appear in.
    * Terminators: br, switch, ret, unreachable, indirectbr, callbr, invoke, resume.
      Phase A supports br, ret and unreachable; anything else becomes UNSUPPORTED_CONSTRUCT.
    * Calls are not terminators (the CFG is intraprocedural, [C2]).
Status
    STUB (Part 2).
"""
