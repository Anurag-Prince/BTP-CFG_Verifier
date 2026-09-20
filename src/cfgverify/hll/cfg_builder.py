"""Basic blocks -> HLL CFG (model.cfg.CFG).

Responsibility
    Build one CFG per function from ir_reader output: one node per block, edges from terminators,
    return blocks connected to a virtual EXIT node.
Verification
    Must agree exactly with LLVM's own view (hll.llvm_view) on every test program. That is our oracle.
Status
    STUB (Part 2).
"""
