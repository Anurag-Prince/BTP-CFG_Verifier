"""Check the IR reader against three independent views that LLVM itself provides.

1. `opt -passes=dot-cfg`: LLVM's CFG. It must have the same blocks, and the same edges with the same successor
   index (successor 0 of a conditional branch is its TRUE target; of a switch, its default; and so on).
2. `; preds = ...` comments: printed by LLVM's own IR writer. They must equal the reader's predecessors, counted
   per edge.
3. llvmlite: LLVM's parser, used through Python. It must see the same number of blocks, the same block names
   (where named), and the same opcode sequence in every block. This catches mistakes in splitting instructions,
   such as multi-line `switch` or `invoke`. Calls to llvm.dbg.* are left out of this comparison, because
   llvmlite's newer LLVM turns them into debug records that are not instructions.

`crosscheck()` returns a list of human-readable discrepancies. An empty list means all three agree with the reader.
Used by `cfgverify parse-ir --crosscheck`, by scripts/checkpoint_part3.sh, and by the tests.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

from cfgverify.hll import llvm_view
from cfgverify.hll.ir_reader import Module, predecessor_counts, read_ir


def _against_dot_cfg(module: Module, ll_path: Path) -> list[str]:
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        dots = llvm_view.export_dot(ll_path, tmp)
        if set(dots) != set(module.functions):
            return [f"dot-cfg: functions {sorted(dots)} != reader {sorted(module.functions)}"]
        for name, fn in module.functions.items():
            g = llvm_view.load_dot(dots[name])
            nodes = {n.lstrip("%") for n in g.nodes}
            if nodes != set(fn.block_names):
                problems.append(f"dot-cfg @{name}: blocks {sorted(nodes)} != reader {sorted(fn.block_names)}")
                continue
            theirs = Counter((u.lstrip("%"), v.lstrip("%"), d["succ_index"]) for u, v, d in g.edges(data=True))
            ours = Counter((b.name, s.target, i) for b in fn.blocks for i, s in enumerate(b.terminator.successors))
            if theirs != ours:
                problems.append(
                    f"dot-cfg @{name}: edges differ; only in LLVM {sorted((theirs - ours).elements())}, "
                    f"only in reader {sorted((ours - theirs).elements())}"
                )
    return problems


def _against_preds_comments(module: Module) -> list[str]:
    problems = []
    for name, fn in module.functions.items():
        counts = predecessor_counts(fn)
        for b in fn.blocks:
            if b.annotated_preds is None:
                continue
            if Counter(b.annotated_preds) != counts[b.name]:
                problems.append(
                    f"preds comment @{name}/%{b.name}: LLVM says {sorted(b.annotated_preds)}, "
                    f"reader computed {sorted(counts[b.name].elements())}"
                )
    return problems


def _against_llvmlite(module: Module, ll_path: Path) -> list[str]:
    import llvmlite.binding as llvm

    problems = []
    lite = llvm.parse_assembly(Path(ll_path).read_text())
    lite_fns = {f.name: f for f in lite.functions if not f.is_declaration}
    if set(lite_fns) != set(module.functions):
        return [f"llvmlite: functions {sorted(lite_fns)} != reader {sorted(module.functions)}"]
    for name, fn in module.functions.items():
        lite_blocks = list(lite_fns[name].blocks)
        if len(lite_blocks) != len(fn.blocks):
            problems.append(f"llvmlite @{name}: {len(lite_blocks)} blocks, reader {len(fn.blocks)}")
            continue
        for lb, b in zip(lite_blocks, fn.blocks):
            if lb.name and lb.name != b.name:
                problems.append(f"llvmlite @{name}: block {lb.name!r} read as {b.name!r}")
            lite_ops = [i.opcode for i in lb.instructions]
            # llvmlite's LLVM (19+) turns llvm.dbg.* calls into debug records, which are not instructions.
            ours = [i.opcode for i in b.instructions if not i.is_debug_intrinsic]
            if lite_ops != ours:
                problems.append(f"llvmlite @{name}/%{b.name}: opcodes {lite_ops} != reader {ours}")
    return problems


def crosscheck(ll_path: str | Path, module: Module | None = None, use_llvmlite: bool = True) -> list[str]:
    ll_path = Path(ll_path)
    module = module or read_ir(ll_path)
    problems = _against_dot_cfg(module, ll_path) + _against_preds_comments(module)
    if use_llvmlite:
        problems += _against_llvmlite(module, ll_path)
    return problems
