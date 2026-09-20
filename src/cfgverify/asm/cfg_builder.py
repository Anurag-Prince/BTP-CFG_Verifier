"""Instruction stream -> ASM CFG using the classic leader algorithm.

Leaders: the first instruction; every branch/jump target; every instruction after a branch, jump or return.
Edges:   conditional branch -> target + fall-through; jump -> target; return -> EXIT;
         call -> fall-through; indirect jump -> UNSUPPORTED (Phase A).
ISA quirks (delay slots, predication, multi-branch compare sequences) are handled through hooks
declared in asm/isa/base.py.
Note from probing: at -O0 on some targets, fall-through blocks have NO label (only a `# %bb.N:` comment),
so blocks must come from leaders, not from labels.
Status
    STUB (Part 3). Blocked by [A1]–[A5].
"""
