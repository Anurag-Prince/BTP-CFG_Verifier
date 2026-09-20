"""Normalized operations: one small, ISA-neutral vocabulary for what the instructions in a CFG node do.

Why: raw instructions depend on the source of the CFG and on naming.
    * LLVM IR says `%cmp = icmp slt i32 %1, 0`.
    * Clang's numbered IR says `%6 = icmp slt i32 %5, 0`.
    * Assembly says something ISA-specific.
A normalized operation drops types and replaces every local name with a canonical one, so the same program
always gives the same operations:
    v2 = CMP.lt.s v1, 0

This is *operation* normalization (per instruction, Part 4). It is different from *CFG* normalization
(rules N1-N8, which change graph structure, in a later part).

Canonical names inside one function:
    a0, a1, ...    function arguments, by position
    s0, s1, ...    stack slots (IR `alloca`), in order of appearance
    v0, v1, ...    every other value an instruction defines, in order of appearance
    B0, B1, ...    basic blocks, in file order (B0 is the entry)
    @name          globals and functions keep their names; constants are kept as written
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# The vocabulary. The HLL side (hll/ir_ops.py) and, later, the assembly side map their instructions onto it.
OP_CLASSES = {
    "ALLOC": "reserve a stack slot (IR alloca)",
    "LOAD": "read memory",
    "STORE": "write memory",
    "ARITH": "integer/float arithmetic or bitwise operation (kind: add, sub, mul, div, rem, and, or, xor, shl, shr, neg)",
    "CMP": "comparison producing a truth value (kind: eq, ne, lt, le, gt, ge + .s signed / .u unsigned / .f float)",
    "CAST": "value conversion (kind: sext, zext, trunc, fptosi, sitofp, ...)",
    "ADDR": "address computation (IR getelementptr)",
    "CALL": "call (kind: callee name)",
    "PHI": "value chosen by the incoming edge",
    "SELECT": "value chosen by a condition, without branching",
    "JUMP": "unconditional transfer to one block (terminator)",
    "BRANCH": "two-way conditional transfer (terminator)",
    "SWITCH": "multi-way transfer on a value (terminator)",
    "RETURN": "leave the function (terminator)",
    "UNREACHABLE": "control never reaches here (terminator)",
    "INDIRECT": "indirect/exceptional transfer: indirectbr, invoke, callbr, resume, EH (terminator)",
    "OTHER": "anything not listed above (kept, never dropped)",
}
TERMINATOR_CLASSES = frozenset({"JUMP", "BRANCH", "SWITCH", "RETURN", "UNREACHABLE", "INDIRECT"})


@dataclass(frozen=True)
class Operation:
    op: str                    # one of OP_CLASSES
    kind: str | None           # refinement, for example "add", "lt.s", "square"
    result: str | None         # canonical name of the defined value (v3, s0) or None
    operands: tuple[str, ...]  # canonical operands (v1, 0, @g, B2, ...)
    text: str                  # one-line rendering, for example "v2 = CMP.lt.s v1, 0"
    source_line: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["operands"] = list(self.operands)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Operation":
        return cls(d["op"], d.get("kind"), d.get("result"), tuple(d.get("operands", ())), d["text"],
                   d.get("source_line"))


def render(op: str, kind: str | None, result: str | None, operands: tuple[str, ...]) -> str:
    """The canonical one-line text of an operation (also used as its identity in tests)."""
    name = f"{op}.{kind}" if kind else op
    if op == "STORE" and len(operands) == 2:
        body = f"{name} {operands[0]} -> {operands[1]}"
    elif op == "BRANCH" and len(operands) == 3:
        body = f"{name} {operands[0]} ? {operands[1]} : {operands[2]}"
    elif op == "CALL":
        body = f"{name}({', '.join(operands)})"
    else:
        body = f"{name} {', '.join(operands)}".rstrip()
    return f"{result} = {body}" if result else body
