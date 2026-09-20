"""The contract every ISA/dialect plug-in must fulfil (docs/ARCHITECTURE.md §7.3).

A plug-in must describe:
    * syntax: comment markers, label pattern, local-label forms (for example `.LBB0_3`, `1:` with `1f`/`1b`),
      function-boundary conventions for its object format ([A5]);
    * classify(instruction) -> COND_BRANCH | JUMP | INDIRECT_JUMP | CALL | RETURN | OTHER;
    * branch_targets(instruction) -> list of label names;
    * quirks: delay slots (MIPS/SPARC), predication (ARM32/Thumb IT), multi-instruction condition
      sequences (for example x86 floating-point `==` -> `jne` + `jp`), wide-integer compare splitting;
    * later (L4 polarity check): condition-code semantics of each conditional branch.
Status
    STUB. The interface will be written as an abstract base class in Part 3, once [A1]/[A2] are answered.
"""
