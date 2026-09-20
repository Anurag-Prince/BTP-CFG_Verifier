"""cfgverify: checks whether the control flow of assembly matches the C/HLL source it came from.

Pipeline (see docs/ARCHITECTURE.md §5):
    C --clang--> LLVM IR --> HLL CFG --normalize--+
                                                  +--> match --> MATCH / MISMATCH --> (AI explanation)
    Assembly -----------------> ASM CFG --normalize--+

The deterministic matcher is the only authority on the verdict. The AI layer only explains evidence.
"""

__version__ = "0.1.0"
