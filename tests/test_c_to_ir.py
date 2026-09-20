"""Part 1 checkpoint: C -> LLVM IR works, and the graph + visualization stack works on that IR."""

import re
import subprocess

from conftest import EXPECTED_DECISIONS

from cfgverify import toolchain
from cfgverify.hll import llvm_view
from cfgverify.hll.frontend import ir_target_triple


def test_native_build_and_run(example_c, tmp_path):
    """Clang can compile, link and run a C program (headers, libc and linker are all present)."""
    exe = tmp_path / "hello_cfg"
    clang = toolchain.require_tool("clang")
    build = subprocess.run([clang, "-O0", "-Wall", "-Wextra", "-Werror", str(example_c), "-o", str(exe)],
                           capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
    assert "sum_even_upto(10) = 30" in out
    assert "classify(50) = 1, classify(-5) = -1, classify(7) = 0" in out
    assert "count_digits(-12345) = 5" in out


def test_ir_is_textual_llvm_with_all_functions(example_ir):
    text = example_ir.read_text()
    assert ir_target_triple(example_ir), "IR has no target triple line"
    for fn in EXPECTED_DECISIONS:
        assert re.search(rf"^define .*@{fn}\(", text, re.MULTILINE), f"no definition of {fn} in IR"


def test_ir_keeps_readable_block_names(example_ir):
    """-fno-discard-value-names must be in effect; otherwise blocks are just numbers."""
    text = example_ir.read_text()
    assert "for.cond:" in text and "if.then" in text


def test_llvmlite_reads_the_ir(example_ir):
    blocks = toolchain.llvmlite_parse_check(example_ir)
    assert set(blocks) == set(EXPECTED_DECISIONS)
    assert all(n >= 1 for n in blocks.values())


def test_llvm_reference_cfg_matches_source_decisions(example_ir, tmp_path):
    """LLVM's own CFG (opt dot-cfg), loaded into networkx: one 2-way branch per source decision."""
    dots = llvm_view.export_dot(example_ir, tmp_path)
    assert set(dots) == set(EXPECTED_DECISIONS)
    for fn, dot_path in dots.items():
        g = llvm_view.load_dot(dot_path)
        s = llvm_view.summarize(g)
        assert s["two_way_branches"] == EXPECTED_DECISIONS[fn], (fn, s)
        assert s["exit_blocks"] == 1, (fn, s)  # clang -O0 merges returns into one block
        assert s["cyclomatic"] == EXPECTED_DECISIONS[fn] + 1, (fn, s)
        assert "entry" in g, "block names were not recovered from the DOT labels"


def test_svg_rendering(example_ir, tmp_path):
    dots = llvm_view.export_dot(example_ir, tmp_path)
    svg = llvm_view.render_svg(dots["classify"])
    assert "<svg" in svg.read_text()
