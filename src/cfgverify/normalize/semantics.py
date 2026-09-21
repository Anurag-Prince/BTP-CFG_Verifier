"""Part 6, layer 1: **semantic operations** — the vocabulary in which the two sides are compared.

Rule for the whole project: *never compare raw C, raw LLVM IR or raw assembly text*. A block's meaning is the
sequence of semantic categories its instructions fall into, with every register, SSA name, type and label
removed. `movl $1, %ecx`, `mov w8, #1` and `store i32 1, ptr %i` are all one thing: **ASSIGN**.

Two levels already exist below this one:

    raw instruction        `addl $1, %ecx`              ISA- and syntax-specific          (Parts 3, 5)
    normalized operation   `ARITH.add $1, %ecx`         one vocabulary, operands kept     (Parts 4, 5)
    SEMANTIC operation     `INCREMENT`                  comparable across the two sides   (Part 6, here)

The categories named in the project brief are mandatory and are marked `brief` below. Everything a real program
also contains — `%`, `&`, `<<`, casts, `switch`, `alloca`, … — needs a category too, or normalization would
silently drop instructions. Those are marked `extension` and are **TO CONFIRM WITH PROFESSOR [C5]**; none of
them changes the meaning of the brief's categories, and each is listed in `docs/NORMALIZATION.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cfgverify.model.ops import Operation

# --------------------------------------------------------------------------------- the vocabulary
SEMANTIC_CLASSES: dict[str, tuple[str, str]] = {
    # name                    origin       meaning
    "ASSIGN":                ("brief",     "give a value to a variable or register, with no computation"),
    "ADD":                   ("brief",     "addition"),
    "SUBTRACT":              ("brief",     "subtraction"),
    "MULTIPLY":              ("brief",     "multiplication"),
    "DIVIDE":                ("brief",     "division"),
    "INCREMENT":             ("brief",     "add exactly one (i++, ++i, addl $1)"),
    "DECREMENT":             ("brief",     "subtract exactly one (i--, --i, subl $1)"),
    "COMPARE":               ("brief",     "compute a relation between two values"),
    "CONDITIONAL_BRANCH":    ("brief",     "two-way transfer of control on a condition (terminator)"),
    "UNCONDITIONAL_BRANCH":  ("brief",     "one-way transfer of control (terminator)"),
    "CALL":                  ("brief",     "call another function"),
    "RETURN":                ("brief",     "leave the function (terminator)"),
    "LOAD":                  ("brief",     "read a value from memory"),
    "STORE":                 ("brief",     "write a value to memory"),
    # ---- extensions: needed so that no instruction is silently dropped. TO CONFIRM [C5].
    "MODULO":                ("extension", "remainder (%). Not a DIVIDE: it keeps a different result"),
    "NEGATE":                ("extension", "unary minus / two's-complement negation"),
    "BITWISE":               ("extension", "&, |, ^, ~"),
    "SHIFT":                 ("extension", "<<, >>"),
    "CONVERT":               ("extension", "width or type conversion (sext, zext, trunc, int<->float)"),
    "ADDRESS":               ("extension", "address computation (&x, getelementptr, lea)"),
    "ALLOCATE":              ("extension", "reserve a stack slot (alloca, or the assembly prologue)"),
    "SELECT":                ("extension", "choose a value from a condition without branching (?:, cmov)"),
    "PHI":                   ("extension", "value chosen by which edge was taken (SSA only)"),
    "MULTIWAY_BRANCH":       ("extension", "switch / jump table (terminator)"),
    "INDIRECT_BRANCH":       ("extension", "computed or exceptional transfer (terminator)"),
    "UNREACHABLE":           ("extension", "control cannot get here (terminator)"),
    "OTHER":                 ("extension", "anything else: kept, counted and reported, never dropped"),
}

TERMINATORS = frozenset({"CONDITIONAL_BRANCH", "UNCONDITIONAL_BRANCH", "MULTIWAY_BRANCH",
                         "RETURN", "UNREACHABLE", "INDIRECT_BRANCH"})
#: Operations that move no data and compute nothing: they may be dropped when comparing block *content*,
#: because the graph already carries the control flow they describe.
CONTROL_ONLY = frozenset({"CONDITIONAL_BRANCH", "UNCONDITIONAL_BRANCH", "MULTIWAY_BRANCH", "INDIRECT_BRANCH"})
#: Bookkeeping that one side may express and the other may not (SSA vs registers, stack frames).
#: Counted, reported, and excluded from *content* comparison only where a rule says so.
BOOKKEEPING = frozenset({"ALLOCATE", "PHI"})

BRIEF_CLASSES = tuple(n for n, (o, _) in SEMANTIC_CLASSES.items() if o == "brief")
EXTENSION_CLASSES = tuple(n for n, (o, _) in SEMANTIC_CLASSES.items() if o == "extension")

# ------------------------------------------------------------------- normalized operation -> semantic
#: `Operation.op` alone decides the category, except for ARITH, whose `kind` does.
_BY_OP = {
    "ALLOC": "ALLOCATE", "LOAD": "LOAD", "MOVE": "ASSIGN", "STORE": "STORE",
    "CMP": "COMPARE", "CAST": "CONVERT", "ADDR": "ADDRESS", "CALL": "CALL",
    "PHI": "PHI", "SELECT": "SELECT",
    "JUMP": "UNCONDITIONAL_BRANCH", "BRANCH": "CONDITIONAL_BRANCH", "SWITCH": "MULTIWAY_BRANCH",
    "RETURN": "RETURN", "UNREACHABLE": "UNREACHABLE", "INDIRECT": "INDIRECT_BRANCH",
    "OTHER": "OTHER",
}
_BY_ARITH_KIND = {
    "add": "ADD", "sub": "SUBTRACT", "mul": "MULTIPLY",
    "div": "DIVIDE", "div.s": "DIVIDE", "div.u": "DIVIDE",
    "rem": "MODULO", "rem.s": "MODULO", "rem.u": "MODULO",
    "and": "BITWISE", "or": "BITWISE", "xor": "BITWISE", "not": "BITWISE",
    "shl": "SHIFT", "shr": "SHIFT", "shr.s": "SHIFT", "shr.u": "SHIFT",
    "neg": "NEGATE",
}

#: A literal one, however the producer writes it: LLVM `1`, AT&T `$1`, Intel `1`, AArch64 `#1`, RISC-V `1`.
_ONE = re.compile(r"^[$#]?(?:1|0x0*1|0b0*1)$")


def is_one(operand: str) -> bool:
    return bool(_ONE.match(operand.strip()))


def semantic_class(operation: Operation) -> str:
    """The semantic category of one normalized operation. Total: every operation gets a category."""
    if operation.op == "ARITH":
        base = _BY_ARITH_KIND.get((operation.kind or "").lower(), "OTHER")
        if base == "ADD" and any(is_one(o) for o in operation.operands):
            return "INCREMENT"                      # i++ / ++i / addl $1, %ecx / add w8, w8, #1
        if base == "SUBTRACT" and any(is_one(o) for o in operation.operands):
            return "DECREMENT"
        return base
    return _BY_OP.get(operation.op, "OTHER")


@dataclass(frozen=True)
class SemanticOp:
    """One instruction reduced to its meaning. `detail` is kept for reports, never for comparison."""
    kind: str                     # a key of SEMANTIC_CLASSES
    detail: str | None = None     # the normalized operation's own text, for the evidence trail
    source_line: int | None = None

    def __str__(self) -> str:
        return self.kind


def to_semantic(operation: Operation) -> SemanticOp:
    return SemanticOp(semantic_class(operation), operation.text, operation.source_line)


def semantic_ops(operations) -> list[SemanticOp]:
    return [to_semantic(o) for o in operations]


def content_signature(operations, *, drop_bookkeeping: bool = True) -> tuple[str, ...]:
    """A block's comparable *content*: the ordered semantic categories of what it computes.

    Control-only operations are dropped because the graph already carries that information, and ALLOCATE/PHI
    are dropped by default because SSA IR and register assembly disagree about them by construction.
    """
    skip = CONTROL_ONLY | (BOOKKEEPING if drop_bookkeeping else frozenset())
    return tuple(s.kind for s in semantic_ops(operations) if s.kind not in skip)


def content_profile(operations, *, drop_bookkeeping: bool = True) -> dict[str, int]:
    """Order-insensitive version of `content_signature` (instruction scheduling differs between the sides)."""
    profile: dict[str, int] = {}
    for kind in content_signature(operations, drop_bookkeeping=drop_bookkeeping):
        profile[kind] = profile.get(kind, 0) + 1
    return profile
