"""Part 3: the LLVM IR reader (src/cfgverify/hll/ir_reader.py).

1. Hand-written IR snippets: every syntax form the reader must handle, plus malformed input.
2. The regression programs: the reader must reproduce every Part 2 expected CFG exactly.
3. Variants of the same programs (numbered names, -O1/-O2, other targets, LLVM 22's printer): LLVM must agree.
4. Randomly generated C programs: LLVM must agree, and so must the counts the generator knows from the source.
Set CFGVERIFY_FUZZ_N to run more random programs (default 25 per mode; scripts/checkpoint_part3.sh uses more).
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from cfgverify import expected, graph_utils, toolchain
from cfgverify.hll.frontend import emit_llvm_ir
from cfgverify.hll.ir_crosscheck import crosscheck
from cfgverify.hll.ir_reader import IRParseError, parse_ir, predecessor_counts, read_ir

from random_c import random_program

REPO = Path(__file__).resolve().parents[1]
REGRESSION = REPO / "regression"
PROGRAMS = expected.load_all(REGRESSION)
FUNCTIONS = [(p, f) for p in PROGRAMS for f in p.functions]
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "25"))
BUILD = REPO / "build" / "test_ir_reader"


@lru_cache(maxsize=None)
def compile_ir(c_file: Path, tag: str, *flags: str, target: str | None = None) -> Path:
    """C -> IR with the project's canonical flags (cached: several tests read the same program)."""
    return emit_llvm_ir(c_file, BUILD / tag / f"{c_file.stem}.ll", target=target, extra_flags=flags)


def raw_clang_ir(c_file: Path, out: Path, *flags: str) -> Path:
    """Compile WITHOUT the project's canonical flags (for example at -O2, or with Clang's default naming)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    clang = toolchain.require_tool("clang")
    proc = subprocess.run([clang, "-S", "-emit-llvm", *flags, str(c_file), "-o", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return out


# ------------------------------------------------------------------------------------------ 1. syntax forms

NAMED = """
source_filename = "t.c"
target triple = "x86_64-pc-linux-gnu"

define dso_local i32 @f(i32 noundef %x) #0 !dbg !5 {
entry:
  %x.addr = alloca i32, align 4
  %cmp = icmp slt i32 %x, 0, !dbg !9
  br i1 %cmp, label %if.then, label %"if end", !dbg !9

if.then:                                          ; preds = %entry
  br label %"if end", !llvm.loop !7

"if end":                                         ; preds = %if.then, %entry
  ret i32 %x
}

declare i32 @g(i32)

!5 = distinct !DISubprogram(name: "f", line: 3, scopeLine: 3)
!9 = !DILocation(line: 4, column: 9, scope: !5)
"""


def test_named_blocks_terminators_and_roles():
    m = parse_ir(NAMED)
    assert m.source_filename == "t.c" and m.target_triple == "x86_64-pc-linux-gnu"
    assert m.declarations == ["g"]
    fn = m.functions["f"]
    assert fn.block_names == ["entry", "if.then", "if end"]
    assert fn.source_line == 3
    entry = fn.block("entry")
    assert entry.terminator.kind == "cond_br" and entry.terminator.condition == "%cmp"
    assert [(s.target, s.role) for s in entry.terminator.successors] == [("if.then", "T"), ("if end", "F")]
    assert fn.block("if end").predecessors == ["entry", "if.then"]
    assert fn.block("if.then").terminator.successors[0].role == "U"
    assert fn.block("if end").terminator.kind == "ret"
    cond = fn.condition_of("entry")
    assert (cond.opcode, cond.predicate, cond.operands) == ("icmp", "slt", ["i32 %x", "0"])
    assert entry.source_lines == {4}
    assert entry.label_line == 6 and entry.instructions[0].line == 7


UNNAMED = """
define i32 @h(i32 %0, i32 %1) {
  %3 = icmp eq i32 %0, %1
  br i1 %3, label %4, label %5

4:                                                ; preds = %2
  br label %5

5:                                                ; preds = %4, %2
  ret i32 0
}

define void @v(ptr %p, ...) {
  ret void
}
"""


def test_unnamed_entry_takes_the_next_slot_after_the_arguments():
    m = parse_ir(UNNAMED)
    fn = m.functions["h"]
    assert fn.block_names == ["2", "4", "5"]         # two numbered args -> entry is %2
    assert fn.block("5").annotated_preds == ["4", "2"]
    assert Counter(fn.block("5").annotated_preds) == predecessor_counts(fn)["5"]
    assert m.functions["v"].block_names == ["0"]      # a named arg and varargs take no slot


MULTILINE = """
define i32 @s(i32 %x) personality ptr null {
entry:
  switch i32 %x, label %d [
    i32 1, label %a
    i32 2, label %a
    i32 -3, label %b
  ], !dbg !1
a:                                                ; preds = %entry, %entry
  %r = invoke i32 @g()
          to label %b unwind label %lp
b:                                                ; preds = %a, %entry
  callbr void asm "", "r,!i"(i32 %x)
          to label %d [label %c]
c:                                                ; preds = %b
  indirectbr ptr null, [label %d, label %c]
d:                                                ; preds = %b, %c, %entry
  #dbg_value(i32 %x, !2, !DIExpression(), !3)
  %s1 = tail call i32 @g() ; a comment ; with "quotes"
  unreachable
lp:                                               ; preds = %a
  %l = landingpad { ptr, i32 } cleanup
  resume { ptr, i32 } %l
dead:                                             ; No predecessors!
  ret i32 1
}
"""


def test_multiline_and_non_phase_a_terminators():
    fn = parse_ir(MULTILINE).functions["s"]
    roles = {b.name: [(s.target, s.role) for s in b.terminator.successors] for b in fn.blocks}
    assert roles["entry"] == [("d", "default"), ("a", "case:1"), ("a", "case:2"), ("b", "case:-3")]
    assert roles["a"] == [("b", "normal"), ("lp", "unwind")]
    assert roles["b"] == [("d", "default"), ("c", "indirect")]
    assert roles["c"] == [("d", "indirect"), ("c", "indirect")]
    assert [b.terminator.kind for b in fn.blocks] == ["switch", "invoke", "callbr", "indirectbr",
                                                      "unreachable", "resume", "ret"]
    assert [len(b.instructions) for b in fn.blocks] == [1, 1, 1, 1, 2, 2, 1]   # #dbg_value is not an instruction
    assert fn.block("d").instructions[0].opcode == "call"
    assert fn.block("dead").annotated_preds == [] and fn.block("dead").predecessors == []
    assert predecessor_counts(fn)["a"] == Counter({"entry": 2})
    assert {b.name for b in fn.unsupported_terminators()} == {"entry", "a", "b", "c", "lp"}


def test_legacy_numbered_label_comments():
    text = "define void @old(i1 %0) {\n  br i1 %0, label %2, label %3\n; <label>:2:        ; preds = %1\n" \
           "  br label %3\n; <label>:3:        ; preds = %2, %1\n  ret void\n}\n"
    fn = parse_ir(text).functions["old"]
    assert fn.block_names == ["1", "2", "3"]
    assert fn.block("3").predecessors == ["1", "2"]


@pytest.mark.parametrize("text,message", [
    ("define void @f() {\nentry:\n  %x = add i32 1, 2\n}\n", "does not end with a terminator"),
    ("define void @f() {\nentry:\n  br label %nowhere\n}\n", "unknown block"),
    ("define void @f() {\nentry:\n  ret void\n", "not closed"),
    ("define void @f() {\nentry:\n  ret void\n  ret void\n}\n", "middle of block"),
])
def test_malformed_ir_is_rejected_with_a_line_number(text, message):
    with pytest.raises(IRParseError, match=message) as err:
        parse_ir(text, "bad.ll")
    assert "bad.ll:" in str(err.value)


# --------------------------------------------------------------------- 2. the Part 2 regression programs

@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=[f.key for _, f in FUNCTIONS])
def test_reader_reproduces_the_expected_cfg(prog, fn):
    """Blocks, T/F/U edges, exits, predicates, back edges and loops must equal the hand-derived spec."""
    module = read_ir(compile_ir(prog.source, "host"))
    got = module.functions[fn.name]
    assert got.block_names == [b.name for b in fn.blocks], "block names or IR order differ"
    assert Counter((e.src, e.dst, e.role) for e in got.edges()) == Counter((e.src, e.dst, e.kind) for e in fn.edges)
    assert sorted(b.name for b in got.blocks if b.terminator.kind == "ret") == sorted(fn.exits)
    for b in fn.blocks:
        cond = got.condition_of(b.name)
        assert (f"{cond.opcode} {cond.predicate}" if cond else None) == b.ir, b.name
    g = got.to_networkx()
    assert graph_utils.back_edges(g, got.entry.name) == sorted(fn.back_edges)


@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=[f.key for _, f in FUNCTIONS])
def test_reader_source_lines_cover_the_spec(prog, fn):
    got = read_ir(compile_ir(prog.source, "debug", "-g")).functions[fn.name]
    for b in fn.blocks:
        assert set(b.lines) <= got.block(b.name).source_lines, b.name


@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_regression_ir_agrees_with_llvm(prog):
    assert crosscheck(compile_ir(prog.source, "host")) == []
    assert crosscheck(compile_ir(prog.source, "debug", "-g")) == []


# -------------------------------------------------------------------------------------------- 3. variants

def _shape(fn):
    """Edges with block names replaced by their position, so differently named versions can be compared."""
    pos = {b.name: i for i, b in enumerate(fn.blocks)}
    return Counter((pos[e.src], pos[e.dst], e.role) for e in fn.edges())


@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_numbered_block_names_give_the_same_cfg(prog):
    """Clang's default naming (numbers instead of if.then, ...) must not change what the reader extracts."""
    named = read_ir(compile_ir(prog.source, "host"))
    ll = raw_clang_ir(prog.source, BUILD / "numbered" / f"{prog.source.stem}.ll", "-O0", "-fdiscard-value-names")
    numbered = read_ir(ll)
    assert crosscheck(ll, numbered) == []
    for name, fn in named.functions.items():
        assert _shape(numbered.functions[name]) == _shape(fn)


@pytest.mark.parametrize("opt", ["-O1", "-O2"])
@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_optimized_ir_is_read_correctly(prog, opt):
    ll = raw_clang_ir(prog.source, BUILD / opt / f"{prog.source.stem}.ll", opt)
    assert crosscheck(ll) == []


@pytest.mark.parametrize("target", ["riscv64-unknown-elf", "aarch64-unknown-linux-gnu", "mips-unknown-linux-gnu"])
def test_other_targets(target):
    for prog in PROGRAMS:
        assert crosscheck(compile_ir(prog.source, target, target=target)) == []


def test_llvm22_printed_ir_gives_the_same_cfg():
    """llvmlite re-prints the IR with LLVM 22 syntax (for example #dbg_ records). The CFG must not change."""
    import llvmlite.binding as llvm

    for prog in PROGRAMS:
        ll = compile_ir(prog.source, "debug", "-g")
        old = read_ir(ll)
        new = parse_ir(str(llvm.parse_assembly(ll.read_text())))
        for name, fn in old.functions.items():
            other = new.functions[name]
            assert [(e.src, e.dst, e.role) for e in other.edges()] == [(e.src, e.dst, e.role) for e in fn.edges()]
            assert [b.source_lines for b in other.blocks] == [b.source_lines for b in fn.blocks]


# ------------------------------------------------------------------------------------ 4. random programs

def _write(seed: int, extended: bool):
    prog = random_program(seed, extended=extended)
    path = BUILD / "random" / f"{'ext' if extended else 'sup'}_{seed}.c"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prog.source)
    return prog, path


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_supported_programs(seed):
    prog, c_file = _write(seed, extended=False)
    ll = compile_ir(c_file, "random")
    module = read_ir(ll)
    assert crosscheck(ll, module) == []
    for name, fn in module.functions.items():
        assert not fn.unsupported_terminators()
        branches = [b for b in fn.blocks if b.terminator.kind == "cond_br"]
        assert len(branches) == prog.decisions[name], "one conditional branch per source condition"
        for b in branches:  # at -O0 the comparison sits in the same block, right before the branch
            cond = fn.condition_of(b)
            assert cond.opcode == "icmp" and cond.instruction is b.instructions[-2]
        assert len(graph_utils.natural_loops(fn.to_networkx(), fn.entry.name)) == prog.loops[name]
        assert sum(1 for b in fn.blocks if b.terminator.kind == "ret") == 1, "Clang -O0 has one return block"
    for flags in (("-O0", "-fdiscard-value-names"), ("-O2",)):
        other = raw_clang_ir(c_file, BUILD / "random" / f"{c_file.stem}{flags[-1]}.ll", *flags)
        assert crosscheck(other) == []


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_extended_programs(seed):
    """switch, &&, ||, ?:, goto and dead code: not verifiable yet, but the reader must read them right."""
    _, c_file = _write(seed, extended=True)
    assert crosscheck(compile_ir(c_file, "random")) == []
    other = raw_clang_ir(c_file, BUILD / "random" / f"{c_file.stem}-O2.ll", "-O2")
    assert crosscheck(other) == []
