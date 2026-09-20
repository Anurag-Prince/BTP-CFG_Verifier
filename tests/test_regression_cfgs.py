"""Part 2: the regression programs and their expected CFGs.

For every function in regression/c/*.c, the hand-derived expected CFG in regression/expected/*.toml must agree
with LLVM's own CFG (opt -passes=dot-cfg on Clang -O0 IR). The checks cover:
    * the same basic blocks and the same edges, each with the same kind (T / F / U);
    * the same exit blocks, back edges and natural loops (with nesting depth);
    * the same branch conditions (the icmp predicate that feeds each conditional branch);
    * the claimed source lines are executed inside the claimed block (checked with -g debug info);
    * the program behaves as documented (self-check main, compiled with -DREGRESSION_MAIN);
    * the IR CFG does not change with the target triple (the programs include no headers, so any target works).
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from cfgverify import expected, graph_utils, toolchain
from cfgverify.hll import llvm_view
from cfgverify.hll.frontend import emit_llvm_ir

REPO = Path(__file__).resolve().parents[1]
REGRESSION = REPO / "regression"
PROGRAMS = expected.load_all(REGRESSION)
FUNCTIONS = [(p, f) for p in PROGRAMS for f in p.functions]
REQUIRED_PROGRAMS = {
    "sequence.c", "if.c", "if_else.c", "nested_if.c", "while.c",
    "for.c", "do_while.c", "nested_loop.c", "break.c", "continue.c",
}
# Targets with very different ISAs. Only IR is generated for them, never assembly: no ISA is assumed.
OTHER_TARGETS = [
    "riscv64-unknown-elf", "aarch64-unknown-linux-gnu", "i686-unknown-linux-gnu",
    "armv7-unknown-linux-gnueabihf", "mips-unknown-linux-gnu",
]
_BUILD = REPO / "build" / "regression"


def _ids(items):
    return [f.key for _, f in items]


@lru_cache(maxsize=None)
def llvm_graphs(program: str, target: str | None = None) -> dict:
    """{function: networkx graph} from LLVM's dot-cfg for one program (compiled once per target)."""
    tag = target or "host"
    ll = emit_llvm_ir(REGRESSION / "c" / program, _BUILD / tag / f"{Path(program).stem}.ll", target=target)
    dots = llvm_view.export_dot(ll, _BUILD / tag / f"{Path(program).stem}.dot")
    return {fn: llvm_view.load_dot(path) for fn, path in dots.items()}


@lru_cache(maxsize=None)
def debug_lines(program: str) -> dict:
    """{function: {block: set(source lines)}} from a -g build: which source lines each IR block executes."""
    ll = emit_llvm_ir(REGRESSION / "c" / program, _BUILD / "debug" / f"{Path(program).stem}.ll", extra_flags=("-g",))
    text = ll.read_text()
    locs = {m[1]: int(m[2]) for m in re.finditer(r"^!(\d+) = !DILocation\(line: (\d+)", text, re.MULTILINE)}
    result: dict = {}
    fn = block = None
    for line in text.splitlines():
        m = re.match(r"define .*@(\w+)\(", line)
        if m:
            fn, block = m[1], "entry"
            result[fn] = {"entry": set()}
            continue
        if fn is None:
            continue
        if line == "}":
            fn = None
            continue
        m = re.match(r"^([\w.]+):", line)
        if m:
            block = m[1]
            result[fn][block] = set()
            continue
        if "llvm.dbg." in line:  # debug bookkeeping intrinsics carry the declaration line, not executed code
            continue
        result[fn][block].update(locs[d] for d in re.findall(r"!dbg !(\d+)", line) if d in locs)
    return result


def llvm_edges(g) -> Counter:
    """Edges as (src, dst, kind). A block with two successors has T (port 0) and F (port 1); otherwise U."""
    out = Counter()
    for src, dst, data in g.edges(data=True):
        kind = "U" if g.out_degree(src) == 1 else ("T" if data["succ_index"] == 0 else "F")
        out[(src, dst, kind)] += 1
    return out


# --------------------------------------------------------------------------------------------- coverage

def test_all_required_programs_exist_with_specs():
    have_c = {p.name for p in (REGRESSION / "c").glob("*.c")}
    have_spec = {p.program for p in PROGRAMS}
    assert REQUIRED_PROGRAMS <= have_c
    assert have_c == have_spec, "every regression program needs regression/expected/<name>.toml"


@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_every_function_has_a_spec(prog):
    assert set(llvm_graphs(prog.program)) == {f.name for f in prog.functions}


@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_program_behaves_as_documented(prog, tmp_path):
    """Build with the self-check main and run it: exit code 0 means every documented result holds."""
    exe = tmp_path / "selfcheck"
    clang = toolchain.require_tool("clang")
    build = subprocess.run(
        [clang, "-O0", "-Wall", "-Wextra", "-Werror", "-DREGRESSION_MAIN", str(prog.source), "-o", str(exe)],
        capture_output=True, text=True,
    )
    assert build.returncode == 0, build.stderr
    assert subprocess.run([str(exe)]).returncode == 0, f"{prog.program}: self-check failed"


# ----------------------------------------------------------------------------------- spec vs LLVM oracle

@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=_ids(FUNCTIONS))
def test_spec_is_self_consistent(prog, fn):
    g = fn.graph()
    names = [b.name for b in fn.blocks]
    assert len(names) == len(set(names)), "duplicate block"
    assert fn.entry == "entry"
    for e in fn.edges:
        assert e.src in names and e.dst in names, f"edge {e} uses an undeclared block"
        assert e.kind in expected.EDGE_KINDS
    two_way = {b for b in g if g.out_degree(b) == 2}
    assert two_way == {b.name for b in fn.blocks if b.cond}, "cond/ir must be given exactly for 2-way blocks"
    assert fn.decisions == len(two_way)
    for b in two_way:
        kinds = sorted(d["kind"] for _, _, d in g.out_edges(b, data=True))
        assert kinds == ["F", "T"]
    assert sorted(fn.exits) == sorted(b for b in g if g.out_degree(b) == 0)
    assert sorted(fn.back_edges) == graph_utils.back_edges(g, fn.entry)
    loops = graph_utils.natural_loops(g, fn.entry)
    depths = graph_utils.loop_depths(g, fn.entry)
    assert {lp["header"]: (lp["depth"], set(lp["body"])) for lp in fn.loops} == {
        h: (depths[h], body) for h, body in loops.items()
    }


@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=_ids(FUNCTIONS))
def test_blocks_and_edges_match_llvm(prog, fn):
    g = llvm_graphs(prog.program)[fn.name]
    assert set(g.nodes) == {b.name for b in fn.blocks}
    assert llvm_edges(g) == Counter((e.src, e.dst, e.kind) for e in fn.edges)


@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=_ids(FUNCTIONS))
def test_exits_back_edges_and_loops_match_llvm(prog, fn):
    g = llvm_graphs(prog.program)[fn.name]
    assert sorted(b for b in g if g.out_degree(b) == 0) == sorted(fn.exits)
    assert graph_utils.back_edges(g, "entry") == sorted(fn.back_edges)
    depths = graph_utils.loop_depths(g, "entry")
    assert {h: (depths[h], body) for h, body in graph_utils.natural_loops(g, "entry").items()} == {
        lp["header"]: (lp["depth"], set(lp["body"])) for lp in fn.loops
    }


@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=_ids(FUNCTIONS))
def test_branch_conditions_match_ir(prog, fn):
    g = llvm_graphs(prog.program)[fn.name]
    for b in fn.blocks:
        assert llvm_view.branch_predicate(g.nodes[b.name]["ir_text"]) == b.ir, f"block {b.name}"


@pytest.mark.parametrize("prog,fn", FUNCTIONS, ids=_ids(FUNCTIONS))
def test_source_lines_execute_in_claimed_block(prog, fn):
    """Each line listed for a block must appear in that block's debug locations.

    Clang also attaches some extra lines, for example a loop's line on its back-edge jump or a closing brace.
    So the check is 'claimed lines are a subset of the observed lines', not equality.
    """
    observed = debug_lines(prog.program)[fn.name]
    for b in fn.blocks:
        assert set(b.lines) <= observed.get(b.name, set()), f"{b.name}: {b.lines} vs {sorted(observed[b.name])}"


@pytest.mark.parametrize("target", OTHER_TARGETS)
@pytest.mark.parametrize("prog", PROGRAMS, ids=[p.program for p in PROGRAMS])
def test_ir_cfg_is_target_independent(prog, target):
    host = llvm_graphs(prog.program)
    other = llvm_graphs(prog.program, target)
    assert set(other) == set(host)
    for fn in host:
        assert llvm_edges(other[fn]) == llvm_edges(host[fn]), f"{fn} differs for {target}"
        assert set(other[fn].nodes) == set(host[fn].nodes)


# -------------------------------------------------------------------------------------------- the study doc

def test_study_doc_is_up_to_date():
    """The generated regions of docs/CONTROL_FLOW_FUNDAMENTALS.md must match the specs (run `cfgverify expected-cfgs`)."""
    doc = REPO / "docs" / "CONTROL_FLOW_FUNDAMENTALS.md"
    text = doc.read_text()
    assert expected.refresh_doc(text, PROGRAMS) == text, "run: cfgverify expected-cfgs"
    for _, fn in FUNCTIONS:
        assert f"BEGIN GENERATED cfg: {fn.key} " in text, f"{fn.key} is not documented"
        assert (REPO / "docs" / "cfg" / expected.svg_name(fn)).is_file(), f"missing diagram for {fn.key}"
