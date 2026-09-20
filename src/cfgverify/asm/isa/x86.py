"""x86-64 (and 32-bit x86) in GNU/AT&T or Intel syntax. CANDIDATE ONLY: the target is TO CONFIRM ([A1], [A2])."""

from __future__ import annotations

from cfgverify.asm.isa.base import CALL, COND_BRANCH, Decoded, Isa, split_operands

_CC = {
    "e": "eq", "z": "eq", "ne": "ne", "nz": "ne",
    "l": "lt.s", "nge": "lt.s", "le": "le.s", "ng": "le.s",
    "g": "gt.s", "nle": "gt.s", "ge": "ge.s", "nl": "ge.s",
    "b": "lt.u", "c": "lt.u", "nae": "lt.u", "be": "le.u", "na": "le.u",
    "a": "gt.u", "nbe": "gt.u", "ae": "ge.u", "nb": "ge.u", "nc": "ge.u",
    "s": "sign", "ns": "nosign", "o": "overflow", "no": "nooverflow",
    "p": "parity", "pe": "parity", "np": "noparity", "po": "noparity",
    "cxz": "eq0", "ecxz": "eq0", "rcxz": "eq0",
}
_ARITH = {"add": "add", "sub": "sub", "imul": "mul", "mul": "mul", "idiv": "div.s", "div": "div.u",
          "and": "and", "or": "or", "xor": "xor", "shl": "shl", "sal": "shl", "sar": "shr.s", "shr": "shr.u",
          "neg": "neg", "not": "not", "inc": "add", "dec": "sub", "adc": "add", "sbb": "sub", "xadd": "add"}
_SUFFIXES = ("b", "w", "l", "q")


def _strip_suffix(m: str, table) -> str:
    """AT&T writes the operand size as a suffix: movl, cmpq, addw ..."""
    if m in table:
        return m
    if len(m) > 1 and m[-1] in _SUFFIXES and m[:-1] in table:
        return m[:-1]
    return m


class X86(Isa):
    name = "x86-64"
    aliases = ("x86", "x86_64", "amd64", "i386", "x86-32")
    comment_chars = ("#",)
    local_label_prefixes = (".L", "L")
    assemble_target = "x86_64-unknown-linux-gnu"
    detect_markers = ("pushq", "movq", "movl", "retq", "cltq", "cltd", "leaq", "callq", "endbr64")
    conditions = {f"j{cc}": pred for cc, pred in _CC.items()}
    jumps = frozenset({"jmp", "jmpq"})
    calls = frozenset({"call", "callq", "calll"})
    returns = frozenset({"ret", "retq", "retl", "repz retq", "iret"})
    indirect = frozenset()
    arith = _ARITH
    compares = frozenset({"cmp", "test", "ucomiss", "ucomisd", "comiss", "comisd"})
    flag_setters = compares | {"sub", "add", "and", "or", "shl", "sal", "sar", "shr", "dec", "inc", "xor", "neg"}
    loads = frozenset({"pop", "popq", "popl"})
    stores = frozenset({"push", "pushq", "pushl"})
    moves = frozenset({"mov", "movb", "movw", "movl", "movq", "movabs", "movabsq", "xchg"})
    casts = frozenset({"movsbl", "movsbw", "movsbq", "movswl", "movswq", "movslq", "movzbl", "movzbw", "movzbq",
                       "movzwl", "movzwq", "cltq", "cltd", "cqto", "cwtl", "cbtw", "cdqe", "cdq", "cqo", "cwde",
                       "cvtsi2sd", "cvtsi2ss", "cvttsd2si", "cvttss2si", "cvtss2sd", "cvtsd2ss"})
    address = frozenset({"lea", "leal", "leaq"})

    def decode(self, mnemonic: str, operands: str) -> Decoded:
        m = mnemonic.lower()
        if m.startswith("rep") and " " in m:                      # "repz retq"
            m = m.split()[-1]
        if m not in self.conditions and m not in self.jumps and m not in self.calls and m not in self.returns:
            m = _strip_suffix(m, self.compares | set(_ARITH) | self.moves | self.loads | self.stores |
                              self.address | self.casts | self.flag_setters)
        ops = split_operands(operands)
        d = super().decode(m, operands)
        if d.op == "OTHER" and m.startswith(("set", "cmov")):
            d.kind = m                                            # flag-consuming, but not a branch
        if d.op == "MOVE" and ops:                                # mov between register and memory
            src, dst = (ops[0], ops[-1]) if self.syntax == "att" else (ops[-1], ops[0])
            if self._is_memory(src) and not self._is_memory(dst):
                d.op, d.kind = "LOAD", None
            elif self._is_memory(dst):
                d.op, d.kind = "STORE", None
        if d.cls in (CALL,) and d.kind and d.kind.startswith("*"):
            d.kind = "indirect"
        return d

    def branch_targets(self, mnemonic: str, operands: list[str]) -> list[str]:
        if not operands:
            return []
        target = operands[-1].strip()
        if target.startswith("*"):                                # jmp *%rax / jmp *table(,%rax,8)
            return []
        return [target.lstrip("$")] if self.is_label(target.lstrip("$")) else []

    @staticmethod
    def _is_memory(operand: str) -> bool:
        o = operand.strip()
        return "(" in o or o.lower().endswith("ptr") or "[" in o or " ptr " in o.lower()


class X86Intel(X86):
    name = "x86-64-intel"
    aliases = ("x86-intel", "x86_64-intel", "intel")
    syntax = "intel"
    detect_markers = ("dword ptr", "qword ptr", "endbr64")
