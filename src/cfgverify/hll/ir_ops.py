"""LLVM IR instruction -> normalized operation (model/ops.py).

Two steps for each function:
  1. Canonical names. Arguments become a0.., allocas s0.., other values v0.., and blocks B0.. (file order).
     Every `%name` in the instruction text is rewritten, so named and numbered IR give identical operations.
  2. Classification. The opcode picks an operation class (LOAD, ARITH, CMP, ...). Types, alignment and flags
     are dropped, and only the operand values are kept.

Debug bookkeeping (`call @llvm.dbg.*`) produces no operation. Anything unfamiliar becomes OTHER, so an
instruction is never silently lost and the normalizer never fails on valid IR.
"""

from __future__ import annotations

import re

from cfgverify.hll.ir_reader import Function, Instruction
from cfgverify.model.ops import Operation, render

_IDENT = r'(?:[-a-zA-Z$._][-a-zA-Z$._0-9]*|\d+|"(?:[^"\\]|\\.)*")'
_LOCAL_REF = re.compile(rf"%({_IDENT})")
_ATTACHMENTS = re.compile(r"(?:,\s*![A-Za-z_.][\w.]*\s+!(?:\d+|\{[^{}]*\}))+\s*$")

_BINOPS = {
    "add": "add", "sub": "sub", "mul": "mul", "sdiv": "div.s", "udiv": "div.u", "srem": "rem.s", "urem": "rem.u",
    "and": "and", "or": "or", "xor": "xor", "shl": "shl", "lshr": "shr.u", "ashr": "shr.s",
    "fadd": "add.f", "fsub": "sub.f", "fmul": "mul.f", "fdiv": "div.f", "frem": "rem.f",
}
_ICMP = {"eq": "eq", "ne": "ne", "slt": "lt.s", "sle": "le.s", "sgt": "gt.s", "sge": "ge.s",
         "ult": "lt.u", "ule": "le.u", "ugt": "gt.u", "uge": "ge.u"}
_FCMP = {"oeq": "eq.f", "one": "ne.f", "olt": "lt.f", "ole": "le.f", "ogt": "gt.f", "oge": "ge.f",
         "ueq": "eq.fu", "une": "ne.fu", "ult": "lt.fu", "ule": "le.fu", "ugt": "gt.fu", "uge": "ge.fu",
         "ord": "ord.f", "uno": "uno.f", "true": "true.f", "false": "false.f"}
_CASTS = {"trunc", "zext", "sext", "fptrunc", "fpext", "fptoui", "fptosi", "uitofp", "sitofp",
          "ptrtoint", "inttoptr", "bitcast", "addrspacecast"}
_FLAGS = {"nsw", "nuw", "exact", "disjoint", "samesign", "fast", "nnan", "ninf", "nsz", "arcp", "contract",
          "afn", "reassoc", "volatile", "atomic", "inbounds", "nusw", "inrange", "tail", "musttail", "notail"}


def strip_attachments(text: str) -> str:
    """Drop trailing metadata attachments such as `, !dbg !23`."""
    return _ATTACHMENTS.sub("", text)


def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def _split(text: str) -> list[str]:
    parts, depth, start, in_str = [], 0, 0, False
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str:
            if ch in "([{<":
                depth += 1
            elif ch in ")]}>":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
    if text[start:].strip():
        parts.append(text[start:].strip())
    return parts


def _value(piece: str) -> str:
    """The value in a typed operand such as 'i32 noundef v3' -> 'v3'. Constant expressions are kept whole."""
    piece = piece.strip()
    if piece.endswith(")") and "(" in piece:
        return piece.split(None, 1)[1] if " " in piece.split("(", 1)[0] else piece
    return piece.split()[-1] if piece else piece


def canonical_names(fn: Function) -> dict[str, str]:
    """'%name' -> canonical name for every argument, block and defined value of the function."""
    names: dict[str, str] = {}
    for i, p in enumerate(fn.params):
        if p:
            names[p] = f"a{i}"
    for b in fn.blocks:
        names[f"%{b.name}"] = f"B{fn.blocks.index(b)}"
    slots = values = 0
    for b in fn.blocks:
        for inst in b.instructions:
            if inst.result and inst.result not in names:
                if inst.opcode == "alloca":
                    names[inst.result] = f"s{slots}"
                    slots += 1
                else:
                    names[inst.result] = f"v{values}"
                    values += 1
    return names


def rename(text: str, names: dict[str, str]) -> str:
    """Replace every local reference (%x, %7, %"a b", label %if.then) with its canonical name."""

    def sub(m: re.Match) -> str:
        return names.get("%" + _unquote(m.group(1)), m.group(0))

    return _LOCAL_REF.sub(sub, text)


def normalize_instruction(inst: Instruction, fn: Function, names: dict[str, str]) -> Operation | None:
    """One IR instruction -> one Operation. Returns None only for llvm.dbg.* bookkeeping calls."""
    if inst.is_debug_intrinsic:
        return None
    text = rename(_ATTACHMENTS.sub("", inst.text), names)
    result = None
    m = re.match(r"^(\S+)\s*=\s*(.*)$", text)
    if m and inst.result:
        result, text = m.group(1), m.group(2)
    op = inst.opcode
    parts = re.split(rf"\b{re.escape(op)}\b", text, maxsplit=1)
    rest = parts[1] if len(parts) == 2 else ""
    body_words = [w for w in rest.split() if w not in _FLAGS]
    body = " ".join(body_words)

    def make(cls: str, kind: str | None, operands: list[str]) -> Operation:
        ops = tuple(operands)
        return Operation(cls, kind, result, ops, render(cls, kind, result, ops), inst.source_line)

    try:
        if op == "alloca":
            return make("ALLOC", None, [])
        if op == "load":
            return make("LOAD", None, [_value(_split(body)[1])])
        if op == "store":
            p = _split(body)
            return make("STORE", None, [_value(p[0]), _value(p[1])])
        if op in _BINOPS:
            p = _split(body)
            return make("ARITH", _BINOPS[op], [_value(p[0]), _value(p[1])])
        if op == "fneg":
            return make("ARITH", "neg.f", [_value(body)])
        if op in ("icmp", "fcmp"):
            pred, _, operands = body.partition(" ")
            table = _ICMP if op == "icmp" else _FCMP
            p = _split(operands)
            return make("CMP", table.get(pred, pred), [_value(p[0]), _value(p[1])])
        if op in _CASTS:
            return make("CAST", op, [_value(body.rsplit(" to ", 1)[0])])
        if op == "getelementptr":
            p = _split(body)
            return make("ADDR", None, [_value(x) for x in p[1:]])
        if op == "call":
            return _call(text, result, inst)
        if op == "phi":
            pairs = re.findall(r"\[\s*([^,\]]+?)\s*,\s*([^\]]+?)\s*\]", body)
            return make("PHI", None, [f"{v}:{b}" for v, b in pairs])
        if op == "select":
            return make("SELECT", None, [_value(x) for x in _split(body)[:3]])
        if op in ("br", "switch", "ret", "unreachable") or inst.is_terminator:
            return _terminator(inst, fn, names, result)
    except (IndexError, ValueError):
        pass
    # Unknown or unusual form: keep it (operand values only, types dropped), never drop it.
    operands = tuple(_value(x) for x in _split(body)) if body else ()
    return Operation("OTHER", op, result, operands, render("OTHER", op, result, operands), inst.source_line)


def _call(text: str, result: str | None, inst: Instruction) -> Operation:
    m = re.search(r"(@[-a-zA-Z$._0-9]+|@\"[^\"]*\"|\b[vas]\d+)\s*\(", text)
    if not m:
        raise ValueError("call without callee")
    callee = m.group(1)
    depth, start = 0, m.end() - 1
    for k in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[k], 0)
        if depth == 0:
            break
    args = [_value(a) for a in _split(text[start + 1 : k])]
    if callee.startswith("@"):
        kind, operands = _unquote(callee[1:]), args
    else:
        kind, operands = "indirect", [callee, *args]
    ops = tuple(operands)
    return Operation("CALL", kind, result, ops, render("CALL", kind, result, ops), inst.source_line)


def _terminator(inst: Instruction, fn: Function, names: dict[str, str], result: str | None) -> Operation:
    block = next(b for b in fn.blocks if b.terminator.instruction is inst)
    t = block.terminator
    targets = [names[f"%{s.target}"] for s in t.successors]
    cond = rename(t.condition, names) if t.condition else None
    if t.kind == "br":
        cls, kind, operands = "JUMP", None, targets
    elif t.kind == "cond_br":
        cls, kind, operands = "BRANCH", None, [cond, *targets]
    elif t.kind == "switch":
        cls, kind = "SWITCH", None
        operands = [cond] + [f"{s.role.replace('case:', '')}:{names[f'%{s.target}']}" for s in t.successors]
    elif t.kind == "ret":
        body = _ATTACHMENTS.sub("", inst.text).split("ret", 1)[1].strip()
        cls, kind, operands = "RETURN", None, ([] if body == "void" else [rename(_value(body), names)])
    elif t.kind == "unreachable":
        cls, kind, operands = "UNREACHABLE", None, []
    else:
        cls, kind, operands = "INDIRECT", t.kind if t.kind != "eh" else inst.opcode, targets
    ops = tuple(operands)
    return Operation(cls, kind, result, ops, render(cls, kind, result, ops), inst.source_line)
