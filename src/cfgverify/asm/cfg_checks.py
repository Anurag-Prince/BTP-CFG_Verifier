"""Checks every generated assembly CFG must pass (`cfgverify asm-cfg --check`, the Part 5 checkpoint, tests).

Generic checks (shared with the HLL side): JSON schema, JSON and NetworkX round trips, structural invariants,
and one normalized operation per instruction.

Assembly-specific checks:
  * **Compiler block annotations.** Clang marks every machine basic block it emits, either with a `.LBB…` label
    or with a `# %bb.N:` comment. When the input has those marks, our block starts must be exactly that set.
    This is the assembly-side counterpart of the `opt -passes=dot-cfg` oracle used for the HLL side.
  * **The assembler accepts the file**, so we know we parsed real assembly for that architecture.
  * **Branch targets resolve**: every branch goes to a label of the same function, or the block is reported.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from cfgverify.asm.cfg_builder import _leaders
from cfgverify.asm.isa.base import COND_BRANCH, TRANSFERS
from cfgverify.asm.reader import AsmFile
from cfgverify.hll.cfg_checks import check_cfg
from cfgverify.model.cfg import CFG

# Clang names a machine basic block `.LBB<function number>_<block number>`. The pattern must be this strict:
# GCC with -g emits `.LBB2:`/`.LBE2:` too, but those are DWARF *lexical* block markers (a C `{ ... }` scope),
# not basic blocks, and treating them as block starts made this oracle reject correct GCC output.
_LLVM_BLOCK_LABEL = re.compile(r"^\.LBB\d+_\d+$")


def check_block_starts(asm: AsmFile) -> list[str]:
    """Compare our leaders with the machine basic blocks the compiler annotated.

    They are not always identical, and both differences are expected:
      * ours has one extra start when an LLVM machine block ends with TWO terminators (a conditional branch
        followed by an unconditional one, which AArch64 and RISC-V output does constantly). The classic leader
        algorithm splits there; the extra block holds only a jump and normalization (rule N2) removes it later.
      * the compiler has one extra start when it kept a machine block that is only reached by falling through,
        which the leader algorithm merges into its predecessor.
    Anything else is a real disagreement: a missed branch target, or an instruction misclassified as a transfer.
    """
    problems = []
    for name, fn in asm.functions.items():
        marked = fn.marked_block_starts
        labelled = {index for label, index in fn.labels.items() if _LLVM_BLOCK_LABEL.match(label)}
        if not marked and not labelled:
            continue                                   # not compiler-annotated input: nothing to compare with
        expected = {0} | marked | labelled
        decoded = [asm.isa.decode(i.mnemonic, i.operands) for i in fn.instructions]
        ours = set(_leaders(fn, asm.isa)[0])
        for index in sorted(ours - expected):
            if index == 0 or decoded[index - 1].cls != COND_BRANCH:
                problems.append(f"@{name}: we start a block at instruction {index} "
                                f"('{fn.instructions[index].text}') but the compiler did not")
        for index in sorted(expected - ours):
            if index and decoded[index - 1].cls in TRANSFERS:
                problems.append(f"@{name}: the compiler starts a machine block at instruction {index} "
                                f"('{fn.instructions[index].text}') and we do not")
    return problems


def check_assembles(path: str | Path, asm: AsmFile) -> list[str]:
    """The assembler must accept the file for this architecture."""
    target = asm.isa.assemble_target
    if not target:
        return []
    attempts = [["clang", f"--target={target}", "-c", str(path)]]
    if target.startswith("x86_64"):
        attempts.append(["gcc", "-c", str(path)])   # GCC output uses GNU-as spellings clang can reject
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        for command in attempts:
            proc = subprocess.run([*command, "-o", f"{tmp}/out.o"], capture_output=True, text=True)
            if proc.returncode == 0:
                return []
            errors.append(f"{command[0]}: {proc.stderr.strip().splitlines()[:2]}")
    return [f"no assembler accepted the file for {target}: {'; '.join(errors)}"]


def check_targets_resolve(asm: AsmFile) -> list[str]:
    problems = []
    for name, fn in asm.functions.items():
        for instruction in fn.instructions:
            d = asm.isa.decode(instruction.mnemonic, instruction.operands)
            for target in d.targets:
                if target not in fn.labels:
                    problems.append(f"@{name} line {instruction.line}: '{instruction.text}' leaves the function "
                                    f"(label {target} is not defined here)")
    return problems


def check_cfgs(cfgs: dict[str, CFG], asm: AsmFile, path: str | Path | None = None,
               assemble: bool = True) -> dict[str, list[str]]:
    results = {name: check_cfg(cfg) for name, cfg in cfgs.items()}
    for problem in check_block_starts(asm):
        function = problem.split(":")[0].lstrip("@")
        results.setdefault(function, []).append(problem)
    file_problems = (check_assembles(path, asm) if assemble and path else []) + check_targets_resolve(asm)
    for problem in file_problems:
        function = problem.split(" ")[0].lstrip("@") if problem.startswith("@") else next(iter(results), "file")
        results.setdefault(function, []).append(problem)
    return results
