"""Assembly CFG generator: instructions -> basic blocks -> model.cfg.CFG (origin "asm"), one per function.

The algorithm is the classic leader algorithm and is ISA-neutral. The selected plug-in only answers "what is
this instruction" (branch, jump, call, return, other), "where can it go" and "what does it test".

    leaders  = the first instruction
             + every instruction a branch or jump can target
             + the instruction after a conditional branch, jump or return (plus the ISA's delay slots)
    edges    conditional branch -> T (target) and F (fall-through)
             unconditional jump -> U (target); a jump out of the function is an exit (tail call)
             return             -> no successors (exit node)
             indirect jump      -> no successors, recorded as unsupported
             anything else      -> U to the next block (fall-through)

Calls are not terminators: control comes back to the next instruction, so a call never splits a block ([C2]).
"""

from __future__ import annotations

from pathlib import Path

from cfgverify import __version__
from cfgverify.asm.isa.base import COND_BRANCH, INDIRECT_JUMP, JUMP, RETURN, split_operands
from cfgverify.asm.reader import AsmFile, AsmFunction, read_asm
from cfgverify.model.cfg import CFG, BranchInfo, Edge, InstructionRecord, Node

GENERATOR = f"cfgverify {__version__} asm.cfg_builder"


def _leaders(fn: AsmFunction, isa) -> tuple[list[int], list]:
    decoded = [isa.decode(i.mnemonic, i.operands) for i in fn.instructions]
    leaders = {0}
    for index, d in enumerate(decoded):
        for target in d.targets:
            if target in fn.labels:
                leaders.add(fn.labels[target])
        if d.cls in (COND_BRANCH, JUMP, RETURN, INDIRECT_JUMP):
            following = index + 1 + isa.delay_slots
            if following < len(fn.instructions):
                leaders.add(following)
    return sorted(leaders), decoded


def build_cfg(fn: AsmFunction, asm: AsmFile, c_source: str | Path | None = None) -> CFG:
    isa = asm.isa
    leaders, decoded = _leaders(fn, isa)
    block_of = {start: f"B{n}" for n, start in enumerate(leaders)}
    ranges = [(start, leaders[n + 1] if n + 1 < len(leaders) else len(fn.instructions))
              for n, start in enumerate(leaders)]
    c_lines = _read_source_lines(c_source or fn.source_file)

    nodes, edges, exits = [], [], []
    for index, (start, stop) in enumerate(ranges):
        body = fn.instructions[start:stop]
        last, dlast = body[-1], decoded[stop - 1]
        node_id = block_of[start]
        label = body[0].labels[0] if body[0].labels else f"@{body[0].line}"
        operations = [isa.operation(i.mnemonic, i.operands, d, i.line, i.source_line)
                      for i, d in zip(body, decoded[start:stop])]

        successors: list[tuple[str, str]] = []          # (destination block, kind)
        terminator = "br"
        if dlast.cls == COND_BRANCH:
            terminator = "cond_br"
            target = fn.labels.get(dlast.targets[0]) if dlast.targets else None
            if target is not None and stop < len(fn.instructions):
                successors = [(block_of[target], "T"), (block_of[stop], "F")]
            else:
                terminator = "br_external"              # branch out of the function: cannot be followed
        elif dlast.cls == JUMP:
            target = fn.labels.get(dlast.targets[0]) if dlast.targets else None
            successors = [(block_of[target], "U")] if target is not None else []
            terminator = "br" if target is not None else "br_external"
        elif dlast.cls == RETURN:
            terminator = "ret"
        elif dlast.cls == INDIRECT_JUMP:
            terminator = "indirect"
        elif stop < len(fn.instructions):
            successors = [(block_of[stop], "U")]        # ordinary fall-through
        else:
            terminator = "falls_off_end"

        for order, (destination, kind) in enumerate(successors):
            branch = None
            if kind in ("T", "F"):
                compare = _condition(body, decoded[start:stop], isa)
                branch = BranchInfo(
                    taken_when=f"{compare['condition']} is {'true' if kind == 'T' else 'false'}",
                    condition=compare["condition"],
                    compare=compare["compare"],
                    predicate=dlast.predicate,
                    ir=compare["text"],
                    source_line=last.source_line,
                    source_text=c_lines.get(last.source_line) if last.source_line else None,
                )
            edges.append(Edge(f"E{len(edges)}", node_id, destination, order, kind, branch=branch))

        is_exit = not successors
        if is_exit:
            exits.append(node_id)
        nodes.append(Node(
            id=node_id, label=label, index=index,
            instructions=[InstructionRecord(i.text, i.line, i.source_line) for i in body],
            operations=operations,
            predecessors=[], successors=[d for d, _ in dict.fromkeys(successors)],
            terminator=terminator,
            source_lines=sorted({i.source_line for i in body if i.source_line}),
            calls=[o.kind for o in operations if o.op == "CALL" and o.kind],
            is_entry=index == 0, is_exit=is_exit,
        ))

    by_id = {n.id: n for n in nodes}
    for e in edges:
        if e.source not in by_id[e.destination].predecessors:
            by_id[e.destination].predecessors.append(e.source)

    cfg = CFG(
        function=fn.name, origin="asm", entry="B0", exits=exits, nodes=nodes, edges=edges,
        meta={
            "asm_file": asm.path,
            "isa": isa.name,
            "syntax": isa.syntax,
            "c_file": str(c_source) if c_source else fn.source_file,
            "asm_line": fn.start_line,
            "generator": GENERATOR,
            "architecture_status": "TO CONFIRM WITH PROFESSOR ([A1], [A2]); this ISA was selected explicitly",
        },
    )
    return cfg.compute_analysis()


def _condition(body, decoded, isa) -> dict:
    """What the conditional branch at the end of this block tests.

    RISC-V-style ISAs carry the comparison in the branch itself. Flag-based ISAs (x86, AArch64) need the last
    flag-setting instruction of the block.
    """
    branch, dbranch = body[-1], decoded[-1]
    if dbranch.operands:
        operands = ", ".join(dbranch.operands)
        return {"condition": operands, "compare": f"CMP.{dbranch.predicate} {operands}", "text": branch.text}
    for instruction, d in zip(reversed(body[:-1]), reversed(decoded[:-1])):
        if d.sets_flags:
            operands = ", ".join(split_operands(instruction.operands))
            return {"condition": f"flags({instruction.mnemonic} {operands})".strip(),
                    "compare": f"CMP.{dbranch.predicate} {operands}", "text": instruction.text}
    return {"condition": "flags", "compare": f"CMP.{dbranch.predicate}", "text": None}


def build_cfgs(asm: AsmFile, c_source: str | Path | None = None) -> dict[str, CFG]:
    return {name: build_cfg(fn, asm, c_source) for name, fn in asm.functions.items()}


def cfgs_from_file(path: str | Path, isa: str, c_source: str | Path | None = None) -> dict[str, CFG]:
    """Read one .s file with the named ISA ('auto' detects and records the decision) and build every CFG."""
    asm = read_asm(path, isa)
    return build_cfgs(asm, c_source)


def _read_source_lines(c_source) -> dict[int, str]:
    if not c_source:
        return {}
    path = Path(c_source)
    if not path.is_file():
        return {}
    return {i + 1: line.strip() for i, line in enumerate(path.read_text().splitlines())}
