"""Part 5: the assembly CFG generator (asm/reader.py, asm/isa/*, asm/cfg_builder.py).

The verified architecture is still TO CONFIRM WITH PROFESSOR, so the same generator is exercised on three
candidate ISAs, two x86 syntaxes and two compilers. Nothing has a default: every call names the ISA.

1. Unit tests: instruction decoding per ISA, detection, the reader's syntax handling.
2. Hand-written assembly (no compiler directives) with CFGs derived by hand.
3. Every C program in the repo, compiled to every candidate ISA, passes all checks, and its assembly CFG agrees
   with the Part 4 HLL CFG on the number of decisions and loops.
4. GCC output and Intel syntax.
5. Random C programs, same checks. CFGVERIFY_FUZZ_N sets how many (default 6 per ISA).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cfgverify.asm import cfg_builder as asm_builder
from cfgverify.asm import cfg_checks as asm_checks
from cfgverify.asm.isa import UnknownIsa, available, detect, get_isa
from cfgverify.asm.isa.base import CALL, COND_BRANCH, INDIRECT_JUMP, JUMP, RETURN
from cfgverify.asm.reader import parse_asm, read_asm
from cfgverify.cli import main as cli_main
from cfgverify.hll.cfg_builder import cfgs_from_file as hll_cfgs
from cfgverify.model.cfg import CFG, validate_json

from random_c import random_program

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build" / "test_asm_cfg"
ALL_C = sorted((REPO / "regression" / "c").glob("*.c")) + sorted((REPO / "examples" / "c" / "part3").glob("*.c"))
ISAS = {"x86-64": "x86_64-unknown-linux-gnu", "aarch64": "aarch64-unknown-linux-gnu", "riscv": "riscv64-unknown-elf"}
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "6"))
_cache: dict = {}


def compile_asm(c_file: Path, isa: str, *extra: str, compiler: str = "clang", tag: str = "") -> Path:
    key = (c_file, isa, extra, compiler, tag)
    if key in _cache:
        return _cache[key]
    out = BUILD / (tag or isa) / f"{c_file.stem}.s"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [compiler, "-O0", "-g", "-S", str(c_file), "-o", str(out)]
    if compiler == "clang":
        cmd.insert(1, f"--target={ISAS[isa]}")
    cmd[1:1] = list(extra)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    _cache[key] = out
    return out


def asm_cfgs(path: Path, isa: str, c_source: Path | None = None):
    asm = read_asm(path, isa)
    return asm_builder.build_cfgs(asm, c_source=c_source), asm


# ------------------------------------------------------------------------------------------- 1. unit tests

@pytest.mark.parametrize("isa,text,expected", [
    ("x86-64", ("jle", ".LBB0_3"), (COND_BRANCH, "le.s", [".LBB0_3"])),
    ("x86-64", ("jne", ".L2"), (COND_BRANCH, "ne", [".L2"])),
    ("x86-64", ("jmp", ".L4"), (JUMP, None, [".L4"])),
    ("x86-64", ("jmp", "*%rax"), (INDIRECT_JUMP, None, [])),
    ("x86-64", ("callq", "printf@PLT"), (CALL, None, [])),
    ("x86-64", ("retq", ""), (RETURN, None, [])),
    ("aarch64", ("b.gt", ".LBB0_6"), (COND_BRANCH, "gt.s", [".LBB0_6"])),
    ("aarch64", ("cbz", "w8, .LBB0_2"), (COND_BRANCH, "eq0", [".LBB0_2"])),
    ("aarch64", ("b", ".LBB0_1"), (JUMP, None, [".LBB0_1"])),
    ("aarch64", ("br", "x8"), (INDIRECT_JUMP, None, [])),
    ("aarch64", ("bl", "g"), (CALL, None, [])),
    ("aarch64", ("ret", ""), (RETURN, None, [])),
    ("riscv", ("blt", "a0, a1, .LBB0_3"), (COND_BRANCH, "lt.s", [".LBB0_3"])),
    ("riscv", ("bnez", "a0, .LBB0_4"), (COND_BRANCH, "ne0", [".LBB0_4"])),
    ("riscv", ("j", ".LBB0_1"), (JUMP, None, [".LBB0_1"])),
    ("riscv", ("call", "g"), (CALL, None, [])),
    ("riscv", ("ret", ""), (RETURN, None, [])),
    ("riscv", ("jr", "a0"), (INDIRECT_JUMP, None, [])),
])
def test_instruction_decoding(isa, text, expected):
    d = get_isa(isa).decode(*text)
    assert (d.cls, d.predicate, d.targets) == expected


@pytest.mark.parametrize("isa,mnemonic,operands,op", [
    ("x86-64", "movl", "-4(%rbp), %eax", "LOAD"),
    ("x86-64", "movl", "%eax, -4(%rbp)", "STORE"),
    ("x86-64", "movl", "$1, %eax", "MOVE"),
    ("x86-64", "leaq", "8(%rsp), %rdi", "ADDR"),
    ("x86-64", "imull", "%ecx, %eax", "ARITH"),
    ("x86-64", "cmpl", "$0, %edx", "CMP"),
    ("aarch64", "ldr", "w8, [sp, #12]", "LOAD"),
    ("aarch64", "stp", "x29, x30, [sp, #16]", "STORE"),
    ("aarch64", "sdiv", "w9, w8, w10", "ARITH"),
    ("riscv", "sd", "ra, 24(sp)", "STORE"),
    ("riscv", "remw", "a0, a0, a1", "ARITH"),
    ("riscv", "li", "a0, 1", "MOVE"),
])
def test_normalized_operations(isa, mnemonic, operands, op):
    d = get_isa(isa).decode(mnemonic, operands)
    assert d.op == op


def test_isa_must_be_named_and_can_be_detected():
    with pytest.raises(UnknownIsa):
        get_isa("pdp11")
    assert {"x86-64", "aarch64", "riscv"} <= set(available())
    for c_file in [REPO / "regression" / "c" / "while.c"]:
        for isa in ISAS:
            text = compile_asm(c_file, isa).read_text()
            assert detect(text)[0].name == isa


READER_SAMPLE = """
	.text
	.globl	f                       # -- Begin function f
	.type	f,@function
f:                                      # @f
	.file	0 "/tmp" "t.c"
	.loc	0 3 0
# %bb.0:
	pushq	%rbp                    # comment with a label-like word: .Lnot_a_label:
.Ltmp0:
	.loc	0 4 7
	cmpl	$0, %edi
	jle	.LBB0_2
.LBB0_1: movl	$1, %eax                # a label and an instruction on one line
	jmp	.LBB0_3
.LBB0_2:
	movl	$0, %eax
.LBB0_3:
	retq
	.size	f, .-f
"""


def test_reader_handles_labels_comments_and_locations():
    asm = parse_asm(READER_SAMPLE, get_isa("x86-64"))
    fn = asm.functions["f"]
    assert [i.mnemonic for i in fn.instructions] == ["pushq", "cmpl", "jle", "movl", "jmp", "movl", "retq"]
    assert fn.labels == {"f": 0, ".Ltmp0": 1, ".LBB0_1": 3, ".LBB0_2": 5, ".LBB0_3": 6}
    assert fn.instructions[1].source_line == 4 and fn.instructions[0].source_line == 3
    assert fn.marked_block_starts == {0}
    cfg = asm_builder.build_cfg(fn, asm)
    assert [(e.source, e.destination, e.kind) for e in cfg.edges] == [
        ("B0", "B2", "T"), ("B0", "B1", "F"), ("B1", "B3", "U"), ("B2", "B3", "U")]
    assert cfg.node("B0").terminator == "cond_br" and cfg.node("B3").is_exit
    assert cfg.edges[0].branch.predicate == "le.s" and "cmpl" in cfg.edges[0].branch.ir


# ------------------------------------------------------------------------------- 2. hand-written assembly

@pytest.mark.parametrize("isa", list(ISAS))
def test_hand_written_assembly(isa):
    path = REPO / "regression" / "asm" / f"sum_to.{isa}.s"
    cfgs, asm = asm_cfgs(path, isa)
    assert list(cfgs) == ["sum_to"], "the reader must find the function without .globl/.type directives"
    cfg = cfgs["sum_to"]
    assert [problems == [] for problems in asm_checks.check_cfgs(cfgs, asm, path).values()] == [True]
    assert (cfg.analysis["blocks"], cfg.analysis["edges"], cfg.analysis["decisions"]) == (4, 4, 1)
    assert len(cfg.analysis["loops"]) == 1 and len(cfg.analysis["back_edges"]) == 1
    assert [n.terminator for n in cfg.nodes] == ["br", "cond_br", "br", "ret"]
    assert cfg.exits == [cfg.nodes[-1].id]


# --------------------------------------------------------------------- 3. every C program on every ISA

@pytest.mark.parametrize("isa", list(ISAS))
@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_every_program_on_every_candidate_isa(c_file, isa):
    path = compile_asm(c_file, isa)
    cfgs, asm = asm_cfgs(path, isa, c_source=c_file)
    results = asm_checks.check_cfgs(cfgs, asm, path)
    assert results and all(p == [] for p in results.values()), results
    hll = hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll")
    assert set(cfgs) >= set(hll), "every C function must appear in the assembly"
    for name, hll_cfg in hll.items():
        asm_cfg = cfgs[name]
        assert asm_cfg.analysis["decisions"] == hll_cfg.analysis["decisions"], f"{name}: decisions"
        assert len(asm_cfg.analysis["loops"]) == len(hll_cfg.analysis["loops"]), f"{name}: loops"
        assert asm_cfg.analysis["unsupported"] == [] and asm_cfg.analysis["unreachable"] == []
        assert asm_cfg.origin == "asm" and asm_cfg.meta["isa"] == isa


# ----------------------------------------------------------------------- 4. other compiler, other syntax

@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_gcc_output(c_file):
    path = compile_asm(c_file, "x86-64", compiler="gcc", tag="gcc")
    cfgs, asm = asm_cfgs(path, "x86-64", c_source=c_file)
    results = asm_checks.check_cfgs(cfgs, asm, path)
    assert all(p == [] for p in results.values()), results
    hll = hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll")
    for name, hll_cfg in hll.items():
        # GCC folds a branch on a constant (`do { ... } while (1)` becomes a plain jump) where Clang's -O0 IR
        # still carries `br i1 true`; CFG normalization rule N8 folds those on the HLL side later. So the GCC
        # graph may have exactly that many decisions fewer, and no others.
        foldable = sum(1 for n in hll_cfg.nodes
                       for e in hll_cfg.out_edges(n.id)[:1]
                       if n.terminator == "cond_br" and e.branch and e.branch.condition in ("true", "false"))
        expected = hll_cfg.analysis["decisions"]
        assert expected - foldable <= cfgs[name].analysis["decisions"] <= expected, f"{name}: decisions (gcc)"


@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_intel_syntax_gives_the_same_graph(c_file):
    att, _ = asm_cfgs(compile_asm(c_file, "x86-64"), "x86-64")
    intel_path = compile_asm(c_file, "x86-64", "-masm=intel", tag="intel")
    intel, asm = asm_cfgs(intel_path, "x86-64-intel")
    assert all(p == [] for p in asm_checks.check_cfgs(intel, asm, intel_path).values())
    assert set(att) == set(intel)
    for name in att:
        assert [(e.source, e.destination, e.kind) for e in att[name].edges] == \
               [(e.source, e.destination, e.kind) for e in intel[name].edges]
        assert [n.terminator for n in att[name].nodes] == [n.terminator for n in intel[name].nodes]


def test_cli_writes_json_svg_and_gallery(tmp_path):
    path = REPO / "regression" / "asm" / "sum_to.x86-64.s"
    assert cli_main(["asm-cfg", str(path), "--isa", "x86-64", "-o", str(tmp_path), "--check"]) == 0
    doc = (tmp_path / "sum_to.x86-64.x86-64.sum_to.cfg.json").read_text()
    cfg = CFG.from_json(doc)
    validate_json(cfg.to_dict())
    assert cfg.origin == "asm" and cfg.meta["isa"] == "x86-64"
    assert (tmp_path / "sum_to.x86-64.x86-64.sum_to.svg").is_file()
    assert "sum_to" in (tmp_path / "index.html").read_text()


# ------------------------------------------------------------------------------------- 5. random programs

@pytest.mark.parametrize("seed", range(FUZZ_N))
@pytest.mark.parametrize("isa", list(ISAS))
def test_random_programs(isa, seed):
    program = random_program(seed)
    c_file = BUILD / "random" / f"sup_{seed}.c"
    c_file.parent.mkdir(parents=True, exist_ok=True)
    c_file.write_text(program.source)
    path = compile_asm(c_file, isa)
    cfgs, asm = asm_cfgs(path, isa, c_source=c_file)
    assert all(p == [] for p in asm_checks.check_cfgs(cfgs, asm, path).values())
    for name, hll_cfg in hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll").items():
        assert cfgs[name].analysis["decisions"] == hll_cfg.analysis["decisions"], name
        assert len(cfgs[name].analysis["loops"]) == len(hll_cfg.analysis["loops"]), name
