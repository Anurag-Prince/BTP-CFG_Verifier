"""RISC-V (RV32/RV64), GNU syntax. CANDIDATE ONLY: the target is TO CONFIRM ([A1], [A2]).

RISC-V has no flag register: a conditional branch carries its own comparison (`blt a0, a1, .LBB0_3`), so the
branch condition never has to be looked up in an earlier instruction.
"""

from __future__ import annotations

from cfgverify.asm.isa.base import CALL, Decoded, Isa, split_operands


class RiscV(Isa):
    name = "riscv"
    aliases = ("riscv64", "riscv32", "rv64", "rv32", "risc-v")
    comment_chars = ("#",)
    local_label_prefixes = (".L", "L")
    assemble_target = "riscv64-unknown-elf"
    detect_markers = ("addi", "sd", "ld", "lw", "sw", "beqz", "bnez", "jal", "auipc", "li", "mv")
    conditions = {"beq": "eq", "bne": "ne", "blt": "lt.s", "bge": "ge.s", "bltu": "lt.u", "bgeu": "ge.u",
                  "bgt": "gt.s", "ble": "le.s", "bgtu": "gt.u", "bleu": "le.u",
                  "beqz": "eq0", "bnez": "ne0", "blez": "le0", "bgez": "ge0", "bltz": "lt0", "bgtz": "gt0"}
    jumps = frozenset({"j", "jal", "tail"})
    calls = frozenset({"call", "jalr"})
    returns = frozenset({"ret", "mret", "sret"})
    indirect = frozenset({"jr"})
    compares = frozenset({"slt", "slti", "sltu", "sltiu", "seqz", "snez", "sltz", "sgtz",
                          "feq.s", "flt.s", "fle.s", "feq.d", "flt.d", "fle.d"})
    loads = frozenset({"lw", "ld", "lb", "lh", "lbu", "lhu", "lwu", "flw", "fld"})
    stores = frozenset({"sw", "sd", "sb", "sh", "fsw", "fsd"})
    moves = frozenset({"mv", "li", "lui", "fmv.s", "fmv.d", "fmv.w.x"})
    casts = frozenset({"sext.w", "sext.b", "sext.h", "zext.w", "zext.b", "zext.h",
                       "fcvt.w.s", "fcvt.s.w", "fcvt.d.w", "fcvt.w.d"})
    address = frozenset({"auipc", "la", "lla"})
    arith = {"add": "add", "addi": "add", "addw": "add", "addiw": "add", "sub": "sub", "subw": "sub",
             "mul": "mul", "mulw": "mul", "div": "div.s", "divw": "div.s", "divu": "div.u", "divuw": "div.u",
             "rem": "rem.s", "remw": "rem.s", "remu": "rem.u", "remuw": "rem.u", "and": "and", "andi": "and",
             "or": "or", "ori": "or", "xor": "xor", "xori": "xor", "sll": "shl", "slli": "shl", "sllw": "shl",
             "slliw": "shl", "srl": "shr.u", "srli": "shr.u", "sra": "shr.s", "srai": "shr.s", "neg": "neg",
             "negw": "neg", "not": "not", "fadd.s": "add.f", "fsub.s": "sub.f", "fmul.s": "mul.f"}

    def decode(self, mnemonic: str, operands: str) -> Decoded:
        m, ops = mnemonic.lower(), split_operands(operands)
        d = super().decode(m, operands)
        if m == "jal" and len(ops) == 2 and ops[0] not in ("zero", "x0"):
            d.cls, d.targets, d.op, d.kind = CALL, [], "CALL", ops[-1]     # jal ra, f  == call f
        elif m == "jalr":
            d.cls, d.op = CALL, "CALL"
            d.kind = "indirect"
            if ops and ops[0] in ("zero", "x0"):                            # jalr zero, ra, 0 == ret
                d.cls, d.op, d.kind = "RETURN", "RETURN", None
        if d.cls == "COND_BRANCH":
            d.operands = [o for o in ops if o not in d.targets]
        return d

    def branch_targets(self, mnemonic: str, operands: list[str]) -> list[str]:
        if not operands:
            return []
        target = operands[-1].strip()
        return [target] if self.is_label(target) and not target.startswith(("x", "a", "s", "t")) or \
            self.is_label(target) and ("." in target or "_" in target or len(target) > 3) else []
