"""LLVM IR reader: textual .ll -> functions -> basic blocks -> instructions, terminators and edges.

Part 3 module (docs/LLVM_IR_GUIDE.md §8). It reads the *textual* IR that Clang writes and reports, for every
defined function:
    * its basic blocks in file order, with their names (named `if.then` or numbered `5`), and the .ll line numbers;
    * every instruction: opcode, result value, .ll line, and the source line when the IR was built with -g;
    * the terminator: kind, ordered successor targets and each target's role (T / F / U / default / case:<v> / ...),
      and the branch condition operand;
    * successors, predecessors (computed from the terminators, never taken from LLVM's `; preds` comments) and the
      edge list;
    * the instruction that defines each branch condition, for example `icmp slt i32 %1, 0`.

Why a text reader instead of llvmlite (design choice; this reverses the Part 0 proposal):
    * It needs no second copy of LLVM. llvmlite bundles its own LLVM (22), which differs from the system Clang (18),
      and older bundles cannot read IR from newer Clangs.
    * It keeps what evidence needs: exact .ll line numbers, the names exactly as printed, and `!dbg` source lines.
    * It is small and transparent: control flow in IR is fully described by labels and terminators.
Three independent LLVM oracles check it (hll/ir_crosscheck.py): `opt -passes=dot-cfg`, LLVM's own `; preds =`
comments, and llvmlite.

Scope: the reader understands the structure of any function body Clang emits. That includes switch, invoke, callbr
and phi, so it never has to change when the input changes. Whether a construct is *supported for verification*
is decided later (see PHASE_A_TERMINATORS).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# An LLVM local identifier as printed in text: named, numbered, or quoted.
_IDENT = r'(?:[-a-zA-Z$._][-a-zA-Z$._0-9]*|\d+|"(?:[^"\\]|\\.)*")'
_LABEL_RE = re.compile(rf"^(?P<name>{_IDENT}):\s*(?:;(?P<comment>.*))?$")
_LEGACY_LABEL_RE = re.compile(r"^;\s*<label>:(?P<name>\d+):?\s*(?:;(?P<comment>.*))?$")  # LLVM <= 8 style
_DEFINE_RE = re.compile(rf"^define\b[^@]*@(?P<name>{_IDENT})\(")
_DECLARE_RE = re.compile(rf"^declare\b[^@]*@(?P<name>{_IDENT})\(")
_RESULT_RE = re.compile(rf"^(?P<result>%{_IDENT})\s*=\s*(?P<rest>.*)$")
_LABEL_REF_RE = re.compile(rf"label\s+%(?P<name>{_IDENT})")
_DBG_RE = re.compile(r"!dbg\s+!(\d+)")
_DILOC_RE = re.compile(r"^!(\d+)\s*=\s*(?:distinct\s+)?!DILocation\(line:\s*(\d+)")
_DISUB_RE = re.compile(r"^!(\d+)\s*=\s*(?:distinct\s+)?!DISubprogram\((.*)\)\s*$")
_TRIPLE_RE = re.compile(r'^target triple\s*=\s*"([^"]*)"')
_LAYOUT_RE = re.compile(r'^target datalayout\s*=\s*"([^"]*)"')
_SOURCE_RE = re.compile(r'^source_filename\s*=\s*"([^"]*)"')

TERMINATOR_OPCODES = frozenset(
    {"ret", "br", "switch", "indirectbr", "invoke", "callbr", "resume",
     "catchswitch", "catchret", "cleanupret", "unreachable"}
)
# Terminators that the first verification subset (docs/ARCHITECTURE.md §8, Phase A) handles. Anything else is
# still read correctly, but a later stage must report it as UNSUPPORTED_CONSTRUCT.
PHASE_A_TERMINATORS = frozenset({"br", "cond_br", "ret", "unreachable"})
_CALL_MARKERS = {"tail", "musttail", "notail"}


class IRParseError(ValueError):
    """The text is not LLVM IR this reader understands. The message names the .ll line."""

    def __init__(self, message: str, line: int | None = None, path: str | None = None):
        where = f"{path or '<ir>'}:{line}: " if line else ""
        super().__init__(where + message)
        self.line = line


_ATTACHMENTS_RE = re.compile(r"(?:,\s*![A-Za-z_.][\w.]*\s+!(?:\d+|\{[^{}]*\}))+\s*$")


def _without_attachments(text: str) -> str:
    """Drop trailing metadata attachments such as `, !dbg !23` or `, !llvm.loop !6`."""
    return _ATTACHMENTS_RE.sub("", text)


def _unquote(name: str) -> str:
    return name[1:-1] if len(name) >= 2 and name[0] == name[-1] == '"' else name


def _strip_comment(line: str) -> str:
    """Remove a trailing `; comment`, ignoring semicolons inside double-quoted strings."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        elif ch == ";" and not in_str:
            return line[:i].rstrip()
    return line.rstrip()


def _depth_change(text: str) -> int:
    """Net change in ( [ { nesting outside strings. Used to join multi-line instructions such as switch."""
    depth, in_str = 0, False
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
    return depth


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts, depth, in_str, start = [], 0, False, 0
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str:
            if ch in "([{<":
                depth += 1
            elif ch in ")]}>":
                depth -= 1
            elif ch == sep and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


# ---------------------------------------------------------------------------------------------- data model

@dataclass
class Instruction:
    text: str                 # the full instruction, with continuation lines joined and comments removed
    opcode: str               # alloca, load, store, icmp, br, switch, ret, call, phi, ...
    result: str | None        # the value it defines, for example "%cmp" (None for store, br, ret, ...)
    line: int                 # 1-based line in the .ll file where it starts
    dbg: int | None = None    # metadata id from `!dbg !N`
    source_line: int | None = None  # C source line (only when the IR was built with -g)

    @property
    def is_terminator(self) -> bool:
        return self.opcode in TERMINATOR_OPCODES

    @property
    def is_debug_intrinsic(self) -> bool:
        """`call void @llvm.dbg.declare(...)` and friends: debug bookkeeping, no run-time effect.

        LLVM 19+ prints these as `#dbg_...` records instead; the reader skips those records.
        """
        return self.opcode == "call" and "@llvm.dbg." in self.text


@dataclass
class Successor:
    target: str               # block name, for example "if.then" or "12"
    role: str                 # T | F | U | default | case:<value> | normal | unwind | indirect | eh


@dataclass
class Terminator:
    kind: str                 # br | cond_br | ret | unreachable | switch | indirectbr | invoke | callbr | resume | eh
    instruction: Instruction
    successors: list[Successor] = field(default_factory=list)
    condition: str | None = None   # cond_br: the i1 operand ("%cmp", "true", ...); switch: the scrutinee

    @property
    def in_phase_a(self) -> bool:
        return self.kind in PHASE_A_TERMINATORS


@dataclass
class Comparison:
    """The instruction that computes a branch condition."""
    instruction: Instruction
    opcode: str               # icmp | fcmp | trunc | phi | ... (whatever defines the i1 value)
    predicate: str | None     # slt, eq, ... for icmp/fcmp
    operands: list[str]       # typed operands, for example ["i32 %0", "0"]


@dataclass
class BasicBlock:
    name: str
    label_line: int | None    # None for an entry block printed without a label
    instructions: list[Instruction]
    terminator: Terminator
    predecessors: list[str] = field(default_factory=list)       # unique, in file order of the predecessors
    annotated_preds: list[str] | None = None                    # LLVM's `; preds =` comment (cross-checks only)

    @property
    def successors(self) -> list[str]:
        seen: list[str] = []
        for s in self.terminator.successors:
            if s.target not in seen:
                seen.append(s.target)
        return seen

    @property
    def source_lines(self) -> set[int]:
        """C source lines executed by this block (needs -g). llvm.dbg.* calls are left out: they only mark
        where a variable is declared."""
        return {i.source_line for i in self.instructions if i.source_line and not i.is_debug_intrinsic}

    @property
    def body(self) -> list[Instruction]:
        """All instructions except the terminator."""
        return self.instructions[:-1]


@dataclass
class Edge:
    src: str
    dst: str
    role: str


@dataclass
class Function:
    name: str
    signature: str            # the define line without the trailing `{`
    params: list[str]
    line: int                 # line of `define` in the .ll
    blocks: list[BasicBlock]
    source_line: int | None = None  # from the DISubprogram when built with -g
    _by_name: dict = field(default_factory=dict, repr=False)
    _defs: dict = field(default_factory=dict, repr=False)

    @property
    def entry(self) -> BasicBlock:
        return self.blocks[0]

    def block(self, name: str) -> BasicBlock:
        return self._by_name[name]

    @property
    def block_names(self) -> list[str]:
        return [b.name for b in self.blocks]

    def edges(self) -> list[Edge]:
        return [Edge(b.name, s.target, s.role) for b in self.blocks for s in b.terminator.successors]

    def defining_instruction(self, value: str) -> Instruction | None:
        return self._defs.get(value)

    def condition_of(self, block: str | BasicBlock) -> Comparison | None:
        """For a block ending in a conditional branch, the instruction that computes its i1 condition."""
        b = self.block(block) if isinstance(block, str) else block
        if b.terminator.kind != "cond_br" or not b.terminator.condition:
            return None
        inst = self.defining_instruction(b.terminator.condition)
        if inst is None:
            return None
        rest = _without_attachments(_RESULT_RE.match(inst.text).group("rest"))
        tokens = rest.split()
        predicate, first_operand = None, 1
        if inst.opcode in ("icmp", "fcmp"):
            # "icmp [samesign] slt i32 %0, 0" / "fcmp [fast-math flags] olt double %a, %b"
            flags = {"samesign", "fast", "nnan", "ninf", "nsz", "arcp", "contract", "afn", "reassoc"}
            idx = next(k for k in range(1, len(tokens)) if tokens[k] not in flags)
            predicate, first_operand = tokens[idx], idx + 1
        parts = rest.split(None, first_operand)
        operand_text = parts[first_operand] if len(parts) > first_operand else ""
        return Comparison(inst, inst.opcode, predicate, _split_top_level(operand_text))

    def unsupported_terminators(self) -> list[BasicBlock]:
        """Blocks whose terminator is outside the Phase A subset (switch, indirectbr, invoke, ...)."""
        return [b for b in self.blocks if not b.terminator.in_phase_a]

    def to_networkx(self):
        """MultiDiGraph with one node per block and one edge per successor slot (attribute `role`)."""
        import networkx as nx

        g = nx.MultiDiGraph(function=self.name)
        for b in self.blocks:
            g.add_node(b.name)
        for e in self.edges():
            g.add_edge(e.src, e.dst, role=e.role)
        return g


@dataclass
class Module:
    path: str | None
    source_filename: str | None
    target_triple: str | None
    datalayout: str | None
    functions: dict[str, Function]      # definitions only, in file order
    declarations: list[str]             # external functions (declare)


# -------------------------------------------------------------------------------------------------- parsing

def _opcode_and_result(text: str) -> tuple[str, str | None]:
    result = None
    m = _RESULT_RE.match(text)
    if m:
        result, text = m.group("result"), m.group("rest")
    tokens = text.split()
    if not tokens:
        raise ValueError("empty instruction")
    i = 0
    while tokens[i] in _CALL_MARKERS and i + 1 < len(tokens):
        i += 1
    return tokens[i], (_quoted_value(result) if result else None)


def _quoted_value(v: str) -> str:
    """Normalize %"quoted name" to %quoted name so references match."""
    return "%" + _unquote(v[1:])


def _parse_terminator(inst: Instruction, path: str | None) -> Terminator:
    op, text = inst.opcode, inst.text
    body = _without_attachments(_RESULT_RE.match(text).group("rest") if _RESULT_RE.match(text) else text)
    targets = [_unquote(m.group("name")) for m in _LABEL_REF_RE.finditer(body)]

    def fail(msg: str) -> IRParseError:
        return IRParseError(f"{msg}: {text}", inst.line, path)

    if op == "br":
        args = _split_top_level(body[len("br"):].strip())
        if len(args) == 1:
            if len(targets) != 1:
                raise fail("unconditional br without one label")
            return Terminator("br", inst, [Successor(targets[0], "U")])
        if len(args) == 3 and len(targets) == 2:
            cond = args[0].split()[-1]  # "i1 %cmp" -> "%cmp"
            # Textual order is: condition, TRUE target, FALSE target.
            return Terminator("cond_br", inst, [Successor(targets[0], "T"), Successor(targets[1], "F")],
                              condition=_quoted_value(cond) if cond.startswith("%") else cond)
        raise fail("malformed br")
    if op == "ret":
        return Terminator("ret", inst)
    if op == "unreachable":
        return Terminator("unreachable", inst)
    if op == "resume":
        return Terminator("resume", inst)
    if op == "switch":
        head, _, table = body.partition("[")
        default = _LABEL_REF_RE.search(head)
        if not default:
            raise fail("switch without default label")
        scrutinee = _split_top_level(head[len("switch"):].strip())[0].split()[-1]
        succs = [Successor(_unquote(default.group("name")), "default")]
        for case in re.finditer(rf"(?P<val>[^,\[\]]+?),\s*label\s+%(?P<name>{_IDENT})", table):
            value = case.group("val").split()[-1]
            succs.append(Successor(_unquote(case.group("name")), f"case:{value}"))
        return Terminator("switch", inst, succs, condition=scrutinee)
    if op == "indirectbr":
        return Terminator("indirectbr", inst, [Successor(t, "indirect") for t in targets])
    if op == "invoke":
        m = re.search(rf"to\s+label\s+%(?P<n>{_IDENT})\s+unwind\s+(?:label\s+%(?P<u>{_IDENT})|to\s+caller)", body)
        if not m:
            raise fail("malformed invoke")
        succs = [Successor(_unquote(m.group("n")), "normal")]
        if m.group("u"):
            succs.append(Successor(_unquote(m.group("u")), "unwind"))
        return Terminator("invoke", inst, succs)
    if op == "callbr":
        m = re.search(rf"to\s+label\s+%(?P<d>{_IDENT})\s*\[(?P<rest>.*)\]", body)
        if not m:
            raise fail("malformed callbr")
        succs = [Successor(_unquote(m.group("d")), "default")]
        succs += [Successor(_unquote(x.group("name")), "indirect") for x in _LABEL_REF_RE.finditer(m.group("rest"))]
        return Terminator("callbr", inst, succs)
    if op in ("catchswitch", "catchret", "cleanupret"):
        return Terminator("eh", inst, [Successor(t, "eh") for t in targets])
    raise fail(f"unknown terminator {op!r}")


def _param_names(params_text: str) -> list[str]:
    names = []
    for p in _split_top_level(params_text):
        if p == "...":
            continue  # varargs marker, not a value
        last = p.split()[-1] if p.split() else ""
        names.append(last if last.startswith("%") else "")
    return names


def _function_header(lines: list[str], i: int, path: str | None) -> tuple[str, str, str, int]:
    """Return (name, signature, params_text, index of the line holding `{`). Handles headers split over lines."""
    header, j = lines[i].rstrip(), i
    while not _strip_comment(header).rstrip().endswith("{"):
        j += 1
        if j >= len(lines):
            raise IRParseError("function header without '{'", i + 1, path)
        header += " " + lines[j].strip()
    m = _DEFINE_RE.match(header)
    if not m:
        raise IRParseError("cannot read function name", i + 1, path)
    open_paren = m.end() - 1
    depth = 0
    for k in range(open_paren, len(header)):
        if header[k] == "(":
            depth += 1
        elif header[k] == ")":
            depth -= 1
            if depth == 0:
                break
    else:
        raise IRParseError("unbalanced parameter list", i + 1, path)
    signature = _strip_comment(header).rstrip()[:-1].rstrip()
    return _unquote(m.group("name")), signature, header[open_paren + 1 : k], j


def parse_ir(text: str, path: str | None = None) -> Module:
    """Parse textual LLVM IR. Raises IRParseError (with the .ll line number) on malformed input."""
    lines = text.splitlines()
    source = triple = layout = None
    declarations: list[str] = []
    raw_functions: list[tuple[str, str, list[str], int, list]] = []
    locations: dict[int, int] = {}
    subprograms: dict[int, int] = {}
    fn_dbg: dict[str, int] = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        if m := _SOURCE_RE.match(line):
            source = m.group(1)
        elif m := _TRIPLE_RE.match(line):
            triple = m.group(1)
        elif m := _LAYOUT_RE.match(line):
            layout = m.group(1)
        elif m := _DECLARE_RE.match(line):
            declarations.append(_unquote(m.group("name")))
        elif m := _DILOC_RE.match(line):
            locations[int(m.group(1))] = int(m.group(2))
        elif m := _DISUB_RE.match(line):
            ln = re.search(r"\bline:\s*(\d+)", m.group(2))
            if ln:
                subprograms[int(m.group(1))] = int(ln.group(1))
        elif line.startswith("define"):
            name, signature, params_text, body_start = _function_header(lines, i, path)
            if d := _DBG_RE.search(signature):
                fn_dbg[name] = int(d.group(1))
            body, j = [], body_start + 1
            while j < len(lines) and lines[j].rstrip() != "}":
                body.append((j + 1, lines[j]))
                j += 1
            if j >= len(lines):
                raise IRParseError(f"function @{name} is not closed with '}}'", i + 1, path)
            raw_functions.append((name, signature, _param_names(params_text), i + 1, body))
            i = j
        i += 1

    functions: dict[str, Function] = {}
    for name, signature, params, line_no, body in raw_functions:
        fn = _build_function(name, signature, params, line_no, body, path)
        for b in fn.blocks:
            for inst in b.instructions:
                if inst.dbg is not None:
                    inst.source_line = locations.get(inst.dbg) or None
        if name in fn_dbg:
            fn.source_line = subprograms.get(fn_dbg[name])
        functions[name] = fn
    return Module(path, source, triple, layout, functions, declarations)


def _build_function(name, signature, params, line_no, body, path) -> Function:
    # 1) group physical lines into labelled chunks, joining multi-line instructions
    chunks: list[tuple[str | None, int | None, str | None, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] = []
    label = label_line = comment = None
    pending: tuple[int, str] | None = None
    depth = 0

    def flush_pending():
        nonlocal pending
        if pending:
            current.append(pending)
            pending = None

    for ln, raw in body:
        stripped = raw.strip()
        if not stripped:
            continue
        m = None if raw[:1].isspace() else (_LABEL_RE.match(stripped) or _LEGACY_LABEL_RE.match(stripped))
        if m and depth == 0:
            flush_pending()
            if current or label is not None:
                chunks.append((label, label_line, comment, current))
            label, label_line, comment, current = _unquote(m.group("name")), ln, m.group("comment"), []
            continue
        if stripped.startswith(";"):
            continue  # a whole-line comment
        if stripped.startswith("#dbg_") and depth == 0:
            continue  # LLVM >= 19 debug record (e.g. #dbg_declare): metadata, not an instruction
        code = _strip_comment(raw).strip()
        if not code:
            continue
        is_continuation = pending is not None and (depth > 0 or code.startswith(("to ", "]", "unwind ")))
        if is_continuation:
            pending = (pending[0], pending[1] + " " + code)
        else:
            flush_pending()
            pending = (ln, code)
        depth = max(0, _depth_change(pending[1]))
    flush_pending()
    chunks.append((label, label_line, comment, current))

    # 2) the entry block may be printed without a label: it then takes the next unnamed slot after the arguments
    if chunks and chunks[0][0] is None:
        slot = sum(1 for p in params if re.fullmatch(r"%\d+", p))
        _, _, c, insts = chunks[0]
        chunks[0] = (str(slot), None, c, insts)

    # 3) build blocks
    blocks: list[BasicBlock] = []
    defs: dict[str, Instruction] = {}
    for label, label_line, comment, raw_insts in chunks:
        if not raw_insts:
            raise IRParseError(f"block %{label} in @{name} has no instructions", label_line or line_no, path)
        insts = []
        for ln, code in raw_insts:
            try:
                opcode, result = _opcode_and_result(code)
            except ValueError as exc:
                raise IRParseError(str(exc), ln, path) from exc
            dbg = _DBG_RE.search(code)
            inst = Instruction(code, opcode, result, ln, int(dbg.group(1)) if dbg else None)
            insts.append(inst)
            if result:
                defs[result] = inst
        term_inst = insts[-1]
        if not term_inst.is_terminator:
            raise IRParseError(f"block %{label} in @{name} does not end with a terminator", term_inst.line, path)
        for inst in insts[:-1]:
            if inst.is_terminator:
                raise IRParseError(f"terminator in the middle of block %{label}", inst.line, path)
        annotated = None
        if comment is not None:
            c = comment.strip()
            if c.startswith("preds"):
                annotated = [_unquote(p.strip().lstrip("%")) for p in c.split("=", 1)[1].split(",") if p.strip()]
            elif c.startswith("No predecessors"):
                annotated = []
        blocks.append(BasicBlock(label, label_line, insts, _parse_terminator(term_inst, path),
                                 annotated_preds=annotated))

    by_name = {b.name: b for b in blocks}
    if len(by_name) != len(blocks):
        raise IRParseError(f"duplicate block name in @{name}", line_no, path)
    for b in blocks:
        for s in b.terminator.successors:
            if s.target not in by_name:
                raise IRParseError(f"branch to unknown block %{s.target}", b.terminator.instruction.line, path)
            preds = by_name[s.target].predecessors
            if b.name not in preds:
                preds.append(b.name)
    return Function(name, signature, params, line_no, blocks, _by_name=by_name, _defs=defs)


def read_ir(path: str | Path) -> Module:
    path = Path(path)
    return parse_ir(path.read_text(), str(path))


def predecessor_counts(fn: Function) -> dict[str, Counter]:
    """block -> Counter of predecessor blocks, one count per edge (a switch can reach a block twice)."""
    counts: dict[str, Counter] = {b.name: Counter() for b in fn.blocks}
    for e in fn.edges():
        counts[e.dst][e.src] += 1
    return counts


# ------------------------------------------------------------------------------------------------- export

def module_to_dict(module: Module, include_instructions: bool = True) -> dict:
    """A JSON-serialisable view of the module (used by `cfgverify parse-ir --json`)."""

    def comparison(fn: Function, b: BasicBlock):
        c = fn.condition_of(b)
        return None if c is None else {"opcode": c.opcode, "predicate": c.predicate, "operands": c.operands,
                                       "text": c.instruction.text, "line": c.instruction.line}

    out = {
        "path": module.path,
        "source_filename": module.source_filename,
        "target_triple": module.target_triple,
        "declarations": module.declarations,
        "functions": {},
    }
    for fn in module.functions.values():
        blocks = []
        for b in fn.blocks:
            entry = {
                "name": b.name,
                "label_line": b.label_line,
                "terminator": {
                    "kind": b.terminator.kind,
                    "text": b.terminator.instruction.text,
                    "line": b.terminator.instruction.line,
                    "condition": b.terminator.condition,
                    "successors": [{"target": s.target, "role": s.role} for s in b.terminator.successors],
                },
                "condition_defined_by": comparison(fn, b),
                "successors": b.successors,
                "predecessors": b.predecessors,
                "source_lines": sorted(b.source_lines),
                "instruction_count": len(b.instructions),
            }
            if include_instructions:
                entry["instructions"] = [
                    {"line": i.line, "opcode": i.opcode, "result": i.result, "text": i.text,
                     "source_line": i.source_line}
                    for i in b.instructions
                ]
            blocks.append(entry)
        out["functions"][fn.name] = {
            "line": fn.line,
            "source_line": fn.source_line,
            "signature": fn.signature,
            "entry": fn.entry.name,
            "blocks": blocks,
            "edges": [{"src": e.src, "dst": e.dst, "role": e.role} for e in fn.edges()],
        }
    return out
