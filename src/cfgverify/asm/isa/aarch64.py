"""AArch64 (ARM 64-bit), GNU syntax. CANDIDATE ONLY: the target is TO CONFIRM ([A1], [A2])."""

from __future__ import annotations

from cfgverify.asm.isa.base import COND_BRANCH, Decoded, Isa, split_operands

_CC = {"eq": "eq", "ne": "ne", "lt": "lt.s", "le": "le.s", "gt": "gt.s", "ge": "ge.s",
       "lo": "lt.u", "cc": "lt.u", "ls": "le.u", "hi": "gt.u", "hs": "ge.u", "cs": "ge.u",
       "mi": "sign", "pl": "nosign", "vs": "overflow", "vc": "nooverflow", "al": "always"}


class AArch64(Isa):
    name = "aarch64"
    aliases = ("arm64", "arm-64", "aarch64-linux", "armv8")
    comment_chars = ("//",)
    local_label_prefixes = (".L", "L")
    assemble_target = "aarch64-unknown-linux-gnu"
    detect_markers = ("stp", "ldp", "b.eq", "b.ne", "cbz", "cbnz", "adrp", "stur", "ldur")
    conditions = ({f"b.{cc}": pred for cc, pred in _CC.items()} |
                  {"cbz": "eq0", "cbnz": "ne0", "tbz": "bit0", "tbnz": "bit1"})
    jumps = frozenset({"b"})
    calls = frozenset({"bl", "blr"})
    returns = frozenset({"ret"})
    indirect = frozenset({"br", "braa", "brab"})
    compares = frozenset({"cmp", "cmn", "tst", "fcmp", "fcmpe", "ccmp"})
    flag_setters = compares | {"subs", "adds", "ands", "sbcs", "adcs", "negs"}
    loads = frozenset({"ldr", "ldrb", "ldrh", "ldrsw", "ldrsb", "ldrsh", "ldur", "ldursw", "ldp", "ldrw"})
    stores = frozenset({"str", "strb", "strh", "stur", "sturb", "stp", "strw"})
    moves = frozenset({"mov", "movz", "movk", "movn", "fmov", "mvn"})
    casts = frozenset({"sxtw", "sxtb", "sxth", "uxtw", "uxtb", "uxth", "scvtf", "ucvtf", "fcvtzs", "fcvtzu"})
    address = frozenset({"adrp", "adr"})
    arith = {"add": "add", "adds": "add", "sub": "sub", "subs": "sub", "mul": "mul", "madd": "mul",
             "msub": "mul", "sdiv": "div.s", "udiv": "div.u", "and": "and", "ands": "and", "orr": "or",
             "eor": "xor", "lsl": "shl", "lsr": "shr.u", "asr": "shr.s", "neg": "neg", "negs": "neg",
             "fadd": "add.f", "fsub": "sub.f", "fmul": "mul.f", "fdiv": "div.f"}

    def branch_targets(self, mnemonic: str, operands: list[str]) -> list[str]:
        if not operands:
            return []
        target = operands[-1].strip()
        return [target] if self.is_label(target) else []

    def decode(self, mnemonic: str, operands: str) -> Decoded:
        d = super().decode(mnemonic, operands)
        if d.cls == COND_BRANCH:                 # cbz/cbnz/tbz carry their operand; b.cc uses the flags
            d.operands = [o for o in split_operands(operands) if o not in d.targets]
        return d
