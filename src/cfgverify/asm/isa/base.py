"""What an ISA plug-in must describe, and the shared helpers for writing one.

**The verified architecture is still TO CONFIRM WITH PROFESSOR ([A1], [A2]).** Nothing in the project assumes
one: the reader and the CFG builder are ISA-neutral, every ISA lives in its own plug-in here, and the
architecture must be named on every run (`cfgverify asm-cfg --isa ...`) or detected and printed.

A plug-in answers five questions about one instruction:
    1. What class is it?  COND_BRANCH | JUMP | INDIRECT_JUMP | CALL | RETURN | OTHER
    2. Which labels can it jump to?
    3. For a conditional branch, what does it test (a normalized predicate such as lt.s, and, when the ISA keeps
       the comparison in a separate instruction, which earlier instruction sets the flags)?
    4. What is the normalized operation (model/ops.py), so blocks can be compared with the HLL side later?
    5. How is the syntax written: comment characters, local-label prefixes, operand order?
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from cfgverify.model.ops import Operation

COND_BRANCH, JUMP, INDIRECT_JUMP, CALL, RETURN, OTHER = (
    "COND_BRANCH", "JUMP", "INDIRECT_JUMP", "CALL", "RETURN", "OTHER")
TRANSFERS = frozenset({COND_BRANCH, JUMP, INDIRECT_JUMP, RETURN})


@dataclass
class Decoded:
    """What the ISA says about one instruction."""
    cls: str = OTHER
    targets: list[str] = field(default_factory=list)   # label names this instruction can jump to
    predicate: str | None = None                       # normalized condition of a branch (lt.s, ne, eq0, ...)
    operands: list[str] = field(default_factory=list)  # operands of an inline compare-and-branch
    op: str = "OTHER"                                  # normalized operation class (model/ops.py)
    kind: str | None = None                            # its refinement (add, lt.s, callee name, mnemonic, ...)
    sets_flags: bool = False                           # this instruction can define a later branch's condition


def split_operands(text: str) -> list[str]:
    """Split an operand list on top-level commas ( [] () {} and quotes are kept together)."""
    parts, depth, start, quote = [], 0, 0, False
    for i, ch in enumerate(text):
        if ch in "\"'":
            quote = not quote
        elif not quote:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
    if text[start:].strip():
        parts.append(text[start:].strip())
    return parts


class Isa:
    """Base class for an ISA/dialect description. Subclasses set the tables and override decode() if needed."""

    name: str = "?"
    aliases: tuple[str, ...] = ()
    syntax: str = "att"                       # att | intel | unified
    comment_chars: tuple[str, ...] = ("#",)
    local_label_prefixes: tuple[str, ...] = (".L", "L")
    assemble_target: str | None = None        # triple used to check a file with the assembler
    delay_slots: int = 0                      # MIPS/SPARC would set 1; the block builder honours it
    detect_markers: tuple[str, ...] = ()      # mnemonics/directives that strongly suggest this ISA

    # tables filled in by subclasses
    conditions: dict[str, str] = {}           # conditional-branch mnemonic -> normalized predicate
    jumps: frozenset[str] = frozenset()
    calls: frozenset[str] = frozenset()
    returns: frozenset[str] = frozenset()
    indirect: frozenset[str] = frozenset()
    flag_setters: frozenset[str] = frozenset()
    arith: dict[str, str] = {}
    loads: frozenset[str] = frozenset()
    stores: frozenset[str] = frozenset()
    moves: frozenset[str] = frozenset()
    casts: frozenset[str] = frozenset()
    compares: frozenset[str] = frozenset()
    address: frozenset[str] = frozenset()

    # ------------------------------------------------------------------------------------------- decoding
    def decode(self, mnemonic: str, operands: str) -> Decoded:
        m, ops = mnemonic.lower(), split_operands(operands)
        d = Decoded(kind=m)
        if m in self.conditions:
            d.cls, d.predicate, d.targets = COND_BRANCH, self.conditions[m], self.branch_targets(m, ops)
            d.operands = [o for o in ops if o not in d.targets]
            d.op, d.kind = "BRANCH", self.conditions[m]
        elif m in self.jumps:
            d.cls, d.targets, d.op, d.kind = JUMP, self.branch_targets(m, ops), "JUMP", None
            if not d.targets:
                d.cls, d.op, d.kind = INDIRECT_JUMP, "INDIRECT", m
        elif m in self.indirect:
            d.cls, d.op = INDIRECT_JUMP, "INDIRECT"
        elif m in self.returns:
            d.cls, d.op, d.kind = RETURN, "RETURN", None
        elif m in self.calls:
            d.cls, d.op, d.kind = CALL, "CALL", self.callee(ops)
        elif m in self.compares:
            d.op, d.kind, d.sets_flags = "CMP", m, True
        elif m in self.arith:
            d.op, d.kind = "ARITH", self.arith[m]
        elif m in self.loads:
            d.op, d.kind = "LOAD", None
        elif m in self.stores:
            d.op, d.kind = "STORE", None
        elif m in self.moves:
            d.op, d.kind = "MOVE", None
        elif m in self.casts:
            d.op, d.kind = "CAST", m
        elif m in self.address:
            d.op, d.kind = "ADDR", None
        else:
            d.op, d.kind = "OTHER", m
        if m in self.flag_setters:
            d.sets_flags = True
        return d

    def branch_targets(self, mnemonic: str, operands: list[str]) -> list[str]:
        """Labels a branch/jump can go to: by default the last operand, when it looks like a label."""
        if not operands:
            return []
        candidate = operands[-1].lstrip("$*").strip()
        return [candidate] if self.is_label(candidate) else []

    def callee(self, operands: list[str]) -> str | None:
        if not operands:
            return None
        name = operands[-1].lstrip("$*").strip()
        return name.split("@")[0] if self.is_label(name) else "indirect"

    @staticmethod
    def is_label(text: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_.$][\w.$]*", text))

    def is_local_label(self, name: str) -> bool:
        return name.startswith(self.local_label_prefixes)

    # ---------------------------------------------------------------------------------------- operations
    def operation(self, mnemonic: str, operands: str, decoded: Decoded, line: int | None,
                  source_line: int | None = None, targets: list[str] | None = None) -> Operation:
        """Normalized operation for one instruction (model/ops.py vocabulary)."""
        values = tuple(targets if targets is not None else split_operands(operands))
        name = f"{decoded.op}.{decoded.kind}" if decoded.kind else decoded.op
        text = f"{name} {', '.join(values)}".rstrip()
        return Operation(decoded.op, decoded.kind, None, values, text, source_line)

    # ----------------------------------------------------------------------------------------- detection
    @classmethod
    def known_mnemonics(cls) -> frozenset[str]:
        return frozenset(set(cls.conditions) | cls.jumps | cls.calls | cls.returns | cls.indirect |
                         cls.compares | set(cls.arith) | cls.loads | cls.stores | cls.moves | cls.casts |
                         cls.address | set(cls.detect_markers))

    @classmethod
    def score(cls, text: str) -> int:
        """How strongly this text looks like this ISA (used only by --isa auto, which prints its decision).

        Counts the instruction lines whose mnemonic this ISA knows. No file is ever assumed to be one ISA:
        the caller prints the decision and the scores.
        """
        known = cls.known_mnemonics()
        hits = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith((".", "#", "//", "/*", ";")) or stripped.endswith(":"):
                continue
            word = re.split(r"[\s,]", stripped, maxsplit=1)[0].lower()
            hits += word in known
        return hits
