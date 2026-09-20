"""CFG data model shared by the HLL and assembly sides.

Responsibility
    Define one representation that both sides produce, so normalization and matching never need
    to know where a graph came from.
Planned types (to be settled in Part 2)
    BasicBlock   id, origin ("hll" | "asm"), label/name, instruction lines, source lines (optional),
                 terminator kind.
    EdgeKind     UNCONDITIONAL, COND_TRUE / COND_FALSE (HLL), TAKEN / FALLTHROUGH (ASM),
                 RETURN (to virtual EXIT), SWITCH_CASE (Phase B).
    Edge         src, dst, kind.
    CFG          function name, entry id, EXIT id, blocks, edges; backed by networkx.DiGraph.
    Module       mapping from function name to CFG.
Status
    STUB (Part 2).
"""
