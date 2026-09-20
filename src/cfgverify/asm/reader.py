"""ISA-neutral assembly reader: text -> labels, directives, instructions, functions.

Nothing here knows any architecture. The selected ISA plug-in supplies only the syntax details: comment
characters and the local-label prefix. Instruction meaning is decided later, by the same plug-in.

What it keeps per instruction: the mnemonic, the operand text, the line in the `.s` file, the labels attached to
it, the current C source line (from `.loc`, when the assembly was produced with debug info), and whether the
compiler annotated it as the start of a machine basic block (`# %bb.3:`), which is used only to cross-check the
block builder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from cfgverify.asm.isa import Isa, detect, get_isa

_LABEL_RE = re.compile(r"^\s*([A-Za-z_.$][\w.$]*)\s*:\s*(.*)$")
_DIRECTIVE_RE = re.compile(r"^\s*(\.[\w.]+)\s*(.*)$")
_INSTR_RE = re.compile(r"^\s*([A-Za-z][\w.]*)\s*(.*)$")
_LOC_RE = re.compile(r"^\s*(\d+)\s+(\d+)")
_BB_MARKER_RE = re.compile(r"%bb\.(\d+)")


@dataclass
class AsmInstruction:
    mnemonic: str
    operands: str
    text: str
    line: int
    labels: list[str] = field(default_factory=list)
    source_line: int | None = None
    block_marker: bool = False        # the compiler said a machine basic block starts here


@dataclass
class AsmFunction:
    name: str
    instructions: list[AsmInstruction]
    labels: dict[str, int]            # label -> index of the instruction it marks
    start_line: int
    end_line: int
    source_file: str | None = None

    @property
    def marked_block_starts(self) -> set[int]:
        """Instruction indices the compiler annotated as machine-basic-block starts (cross-check only)."""
        return {i for i, inst in enumerate(self.instructions) if inst.block_marker}


@dataclass
class AsmFile:
    path: str | None
    isa: Isa
    functions: dict[str, AsmFunction]
    detection: dict[str, int] | None = None   # scores, when the ISA was detected rather than given


def parse_asm(text: str, isa: Isa, path: str | None = None) -> AsmFile:
    lines = text.splitlines()
    instructions: list[AsmInstruction] = []      # all instructions of the file, in order
    labels_at: dict[int, list[str]] = {}         # instruction index -> labels that mark it
    label_index: dict[str, int] = {}
    function_symbols: list[str] = []
    boundaries: dict[str, int] = {}              # label -> instruction index where a function starts
    ends: list[tuple[str, int]] = []             # (function name, instruction index just past its end)
    source_file: str | None = None
    current_source_line: int | None = None
    pending_labels: list[str] = []
    pending_marker = False

    def strip_comment(raw: str) -> tuple[str, str]:
        best = len(raw)
        for marker in isa.comment_chars:
            pos = raw.find(marker)
            if pos != -1:
                best = min(best, pos)
        return raw[:best], raw[best:]

    for number, raw in enumerate(lines, start=1):
        code, comment = strip_comment(raw)
        if _BB_MARKER_RE.search(comment):
            pending_marker = True
        if not code.strip():
            continue
        while True:
            m = _LABEL_RE.match(code)
            if not m:
                break
            name, rest = m.group(1), m.group(2)
            if name.startswith(".") and not isa.is_local_label(name) and "." in name[1:]:
                break                                   # something like ".text:" is not a label
            pending_labels.append(name)
            label_index[name] = len(instructions)
            labels_at.setdefault(len(instructions), []).append(name)
            if name in function_symbols:
                boundaries[name] = len(instructions)
            code = rest
            if not code.strip():
                break
        if not code.strip():
            continue
        directive = _DIRECTIVE_RE.match(code)
        if directive:
            name, args = directive.group(1), directive.group(2).strip()
            if name == ".loc":
                loc = _LOC_RE.match(args)
                current_source_line = int(loc.group(2)) if loc else current_source_line
            elif name == ".file" and '"' in args:
                quoted = re.findall(r'"([^"]*)"', args)
                source_file = source_file or (quoted[-1] if quoted else None)
            elif name in (".type",):
                parts = [p.strip() for p in args.split(",")]
                if len(parts) >= 2 and "function" in parts[1]:
                    function_symbols.append(parts[0])
            elif name in (".globl", ".global"):
                function_symbols.append(args.split(",")[0].strip())
            elif name == ".size":
                ends.append((args.split(",")[0].strip(), len(instructions)))
            continue
        instr = _INSTR_RE.match(code)
        if not instr:
            continue
        text_line = code.strip()
        instructions.append(AsmInstruction(
            mnemonic=instr.group(1), operands=instr.group(2).strip(), text=text_line, line=number,
            labels=pending_labels, source_line=current_source_line, block_marker=pending_marker,
        ))
        pending_labels, pending_marker = [], False

    functions = _split_functions(instructions, label_index, labels_at, function_symbols, boundaries, ends,
                                 isa, source_file)
    return AsmFile(path, isa, functions)


def _split_functions(instructions, label_index, labels_at, function_symbols, boundaries, ends, isa, source_file):
    starts: list[tuple[int, str]] = sorted({(idx, name) for name, idx in boundaries.items()})
    if not starts:                       # hand-written assembly without .type/.globl: use non-local labels
        starts = sorted({(idx, names[0]) for idx, names in labels_at.items()
                         if idx < len(instructions) and not isa.is_local_label(names[0])})
    if not starts:
        starts = [(0, Path(source_file).stem if source_file else "unnamed")]
    end_of = dict(ends)

    functions: dict[str, AsmFunction] = {}
    for position, (start, name) in enumerate(starts):
        stop = starts[position + 1][0] if position + 1 < len(starts) else len(instructions)
        stop = min(stop, end_of.get(name, stop))
        body = instructions[start:stop]
        if not body:
            continue
        local_labels = {label: idx - start for label, idx in label_index.items() if start <= idx < stop}
        functions[name] = AsmFunction(
            name=name, instructions=body, labels=local_labels,
            start_line=body[0].line, end_line=body[-1].line, source_file=source_file,
        )
    return functions


def read_asm(path: str | Path, isa: str | Isa) -> AsmFile:
    """Read a .s file. `isa` is an ISA name or 'auto'; nothing is ever assumed silently."""
    path = Path(path)
    text = path.read_text()
    detection = None
    if isinstance(isa, str):
        if isa.strip().lower() == "auto":
            isa, detection = detect(text)
        else:
            isa = get_isa(isa)
    result = parse_asm(text, isa, str(path))
    result.detection = detection
    return result
