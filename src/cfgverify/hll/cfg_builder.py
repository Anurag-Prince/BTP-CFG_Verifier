"""HLL CFG generator: LLVM IR (via hll/ir_reader.py) -> model.cfg.CFG, one per function.

    build_cfg(fn, module, c_source=None)      one reader Function -> CFG
    build_cfgs(module, c_source=None)         every function of a module -> {name: CFG}
    cfgs_from_file(path, ...)                 a .c file (compiled with Clang first) or a .ll file -> {name: CFG}

Every function goes through the same code, with no special cases per program or construct.
    Nodes    one per IR basic block, in IR order. The ID is B<index> and the label is the IR name. A node holds
             its raw instructions, its normalized operations (hll/ir_ops.py), its predecessors and successors,
             its terminator kind, its C source lines and calls, and the entry/exit/loop flags.
    Edges    one per successor slot of the terminator, in LLVM's order (index 0 = TRUE target of a 2-way
             branch). Each edge records its kind (T/F/U/default/case:<v>/...), whether it is a back edge, and
             branch information: the tested value, the normalized compare, the IR compare, and the C line and
             its text.
    Analysis decisions, cyclomatic complexity, back edges, natural loops with depth, unreachable blocks, and
             terminators outside the supported subset.
"""

from __future__ import annotations

from pathlib import Path

from cfgverify import __version__
from cfgverify.hll import ir_ops
from cfgverify.hll.ir_reader import Function, Module, read_ir
from cfgverify.model.cfg import CFG, BranchInfo, Edge, InstructionRecord, Node

GENERATOR = f"cfgverify {__version__} hll.cfg_builder"


def _taken_when(role: str, condition: str | None) -> str:
    if role == "T":
        return f"{condition} is true"
    if role == "F":
        return f"{condition} is false"
    if role == "default":
        return "no case matches"
    if role.startswith("case:"):
        return f"{condition} == {role[5:]}"
    return {"normal": "call returns normally", "unwind": "call raises an exception",
            "indirect": "indirect target", "eh": "exception-handling transfer"}.get(role, role)


def build_cfg(fn: Function, module: Module | None = None, c_source: str | Path | None = None) -> CFG:
    names = ir_ops.canonical_names(fn)
    ids = {b.name: f"B{i}" for i, b in enumerate(fn.blocks)}
    c_lines = _read_c_lines(c_source)

    ops_of: dict[int, object] = {}          # id(Instruction) -> Operation (to look up compares)
    nodes: list[Node] = []
    for i, b in enumerate(fn.blocks):
        operations = []
        for inst in b.instructions:
            op = ir_ops.normalize_instruction(inst, fn, names)
            if op is not None:
                operations.append(op)
                ops_of[id(inst)] = op
        nodes.append(Node(
            id=ids[b.name],
            label=b.name,
            index=i,
            instructions=[InstructionRecord(inst.text, inst.line, inst.source_line) for inst in b.instructions],
            operations=operations,
            predecessors=[ids[p] for p in b.predecessors],
            successors=[ids[s] for s in b.successors],
            terminator=b.terminator.kind,
            source_lines=sorted(b.source_lines),
            calls=[o.kind for o in operations if o.op == "CALL" and o.kind],
            is_entry=(i == 0),
            is_exit=(b.terminator.kind in ("ret", "unreachable", "resume")),
        ))

    edges: list[Edge] = []
    for b in fn.blocks:
        t = b.terminator
        cond = ir_ops.rename(t.condition, names) if t.condition else None
        comparison = fn.condition_of(b) if t.kind == "cond_br" else None
        defining = fn.defining_instruction(t.condition) if t.kind == "switch" and t.condition else None
        branch_line = t.instruction.source_line
        for index, s in enumerate(t.successors):
            branch = None
            if s.role != "U":
                compare_inst = comparison.instruction if comparison else defining
                compare_op = ops_of.get(id(compare_inst)) if compare_inst else None
                branch = BranchInfo(
                    taken_when=_taken_when(s.role, cond),
                    condition=cond,
                    compare=compare_op.text if compare_op else None,
                    predicate=comparison.predicate if comparison else None,
                    ir=ir_ops.strip_attachments(compare_inst.text) if compare_inst else None,
                    case_value=s.role[5:] if s.role.startswith("case:") else None,
                    source_line=branch_line,
                    source_text=c_lines.get(branch_line) if branch_line else None,
                )
            edges.append(Edge(f"E{len(edges)}", ids[b.name], ids[s.target], index, s.role, branch=branch))

    cfg = CFG(
        function=fn.name,
        origin="hll",
        entry="B0",
        exits=[n.id for n in nodes if n.is_exit],
        nodes=nodes,
        edges=edges,
        meta={
            "c_file": str(c_source) if c_source else None,
            "ir_file": module.path if module else None,
            "target_triple": module.target_triple if module else None,
            "ir_line": fn.line,
            "source_line": fn.source_line,
            "generator": GENERATOR,
            # canonical name -> name in the IR, so every operation can be traced back (a0 -> %n, s2 -> %found)
            "names": {canon: ir for ir, canon in names.items()},
        },
    )
    return cfg.compute_analysis()


def build_cfgs(module: Module, c_source: str | Path | None = None) -> dict[str, CFG]:
    return {name: build_cfg(fn, module, c_source) for name, fn in module.functions.items()}


def cfgs_from_file(path: str | Path, ll_out: str | Path | None = None, target: str | None = None,
                   debug_info: bool = True) -> dict[str, CFG]:
    """A .c file (compiled with the canonical flags, plus -g by default for C line numbers) or a .ll file."""
    from cfgverify.hll.frontend import emit_llvm_ir

    path = Path(path)
    if path.suffix == ".c":
        ll = emit_llvm_ir(path, ll_out, target=target, extra_flags=("-g",) if debug_info else ())
        return build_cfgs(read_ir(ll), c_source=path)
    return build_cfgs(read_ir(path))


def _read_c_lines(c_source: str | Path | None) -> dict[int, str]:
    if not c_source or not Path(c_source).is_file():
        return {}
    return {i + 1: line.strip() for i, line in enumerate(Path(c_source).read_text().splitlines())}
