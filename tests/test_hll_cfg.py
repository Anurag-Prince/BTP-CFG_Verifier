"""Part 4: the HLL CFG generator (hll/cfg_builder.py, hll/ir_ops.py, model/cfg.py, model/cfg_dot.py).

1. Every regression function's CFG equals its Part 2 expected CFG (nodes, edges, kinds, exits, back edges,
   loops, predicates, C lines).
2. Every C file in the repo produces CFGs that pass all checks (schema, JSON and NetworkX round trips,
   invariants, agreement with LLVM dot-cfg).
3. Normalized operations do not depend on naming: named and numbered IR give identical operations and graphs.
4. Operation normalization, JSON schema, invariants and rendering unit tests.
5. Random C programs (supported and extended, -O0 and -O2) go through the same implementation and pass the same
   checks. CFGVERIFY_FUZZ_N sets how many (default 20 per mode).
"""

from __future__ import annotations

import copy
import os
import subprocess
from pathlib import Path

import jsonschema
import pytest

from cfgverify import expected, toolchain
from cfgverify.cli import main as cli_main
from cfgverify.hll.cfg_builder import build_cfgs, cfgs_from_file
from cfgverify.hll.cfg_checks import check_cfgs
from cfgverify.hll.ir_reader import parse_ir, read_ir
from cfgverify.model import cfg_dot
from cfgverify.model.cfg import CFG, validate_json

from random_c import random_program

REPO = Path(__file__).resolve().parents[1]
PROGRAMS = expected.load_all(REPO / "regression")
FUNCTIONS = [(p, f) for p in PROGRAMS for f in p.functions]
ALL_C = sorted((REPO / "regression" / "c").glob("*.c")) + sorted((REPO / "examples" / "c" / "part3").glob("*.c")) \
    + [REPO / "examples" / "c" / "hello_cfg.c"]
BUILD = REPO / "build" / "test_hll_cfg"
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "20"))
_cache: dict = {}


def cfgs_for(c_file: Path, debug_info: bool = True) -> tuple[dict, Path]:
    key = (c_file, debug_info)
    if key not in _cache:
        ll = BUILD / ("g" if debug_info else "nog") / f"{c_file.stem}.ll"
        _cache[key] = (cfgs_from_file(c_file, ll_out=ll, debug_info=debug_info), ll)
    return _cache[key]


def clang(c_file: Path, out: Path, *flags: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([toolchain.require_tool("clang"), "-S", "-emit-llvm", *flags, str(c_file), "-o", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return out


# ------------------------------------------------------------------------------ 1. Part 2 expected CFGs

@pytest.mark.parametrize("prog,spec", FUNCTIONS, ids=[f.key for _, f in FUNCTIONS])
def test_cfg_equals_the_expected_cfg(prog, spec):
    cfg = cfgs_for(prog.source)[0][spec.name]
    by_label = {n.label: n for n in cfg.nodes}
    lab = {n.id: n.label for n in cfg.nodes}
    assert [n.label for n in cfg.nodes] == [b.name for b in spec.blocks]
    assert [n.id for n in cfg.nodes] == [f"B{i}" for i in range(len(spec.blocks))]
    assert sorted((lab[e.source], lab[e.destination], e.kind) for e in cfg.edges) == \
        sorted((e.src, e.dst, e.kind) for e in spec.edges)
    assert sorted(lab[x] for x in cfg.exits) == sorted(spec.exits)
    assert sorted((lab[a], lab[b]) for a, b in cfg.analysis["back_edges"]) == sorted(spec.back_edges)
    assert {lab[lp["header"]]: (lp["depth"], {lab[x] for x in lp["body"]}) for lp in cfg.analysis["loops"]} == \
        {lp["header"]: (lp["depth"], set(lp["body"])) for lp in spec.loops}
    assert cfg.analysis["decisions"] == spec.decisions
    assert sorted(lab[e.source] + ">" + lab[e.destination] for e in cfg.edges if e.back_edge) == \
        sorted(f"{a}>{b}" for a, b in spec.back_edges)
    for b in spec.blocks:
        node = by_label[b.name]
        assert set(b.lines) <= set(node.source_lines), b.name
        assert node.loop_header == (b.name in {lp["header"] for lp in spec.loops})
        if b.cond:
            branch_edges = [e for e in cfg.edges if e.source == node.id]
            assert [e.kind for e in branch_edges] == ["T", "F"]
            for e in branch_edges:
                assert f"icmp {e.branch.predicate}" == b.ir
                assert e.branch.compare.startswith(f"{e.branch.condition} = CMP.")
                assert e.branch.source_line in b.lines, "the branch is attributed to the condition's C line"
                assert e.branch.source_text and e.branch.source_text.strip()


# ---------------------------------------------------------------------- 2. every C file in the repository

@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_every_c_file_passes_all_cfg_checks(c_file):
    cfgs, ll = cfgs_for(c_file)
    results = check_cfgs(cfgs, ll)
    assert results and all(problems == [] for problems in results.values()), results


# ------------------------------------------------------------------- 3. operations do not depend on names

@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_named_and_numbered_ir_give_identical_operations(c_file):
    named = build_cfgs(read_ir(clang(c_file, BUILD / "named" / f"{c_file.stem}.ll", "-O0", "-fno-discard-value-names")))
    numbered = build_cfgs(read_ir(clang(c_file, BUILD / "numbered" / f"{c_file.stem}.ll", "-O0", "-fdiscard-value-names")))
    assert named.keys() == numbered.keys()
    for name in named:
        a, b = named[name], numbered[name]
        assert [[o.text for o in n.operations] for n in a.nodes] == [[o.text for o in n.operations] for n in b.nodes]
        assert [(e.source, e.destination, e.kind, e.back_edge) for e in a.edges] == \
            [(e.source, e.destination, e.kind, e.back_edge) for e in b.edges]
        assert a.analysis == b.analysis
        assert [n.label for n in a.nodes] != [n.label for n in b.nodes] or len(a.nodes) == 1


# ------------------------------------------------------------------------------------------- 4. unit tests

OPS_IR = """
@g = global i32 0
define i32 @f(i32 %x, ptr %p, double %d) {
entry:
  %s = alloca i32, align 4
  store i32 %x, ptr %s, align 4
  %0 = load i32, ptr %s, align 4, !dbg !7
  %add = add nsw i32 %0, 1
  %q = udiv exact i32 %add, 3
  %sh = ashr i32 %q, 1
  %c = icmp samesign ult i32 %sh, 10
  %fc = fcmp une double %d, 0.000000e+00
  %e = sext i32 %sh to i64
  %a = getelementptr inbounds [4 x i32], ptr %p, i64 0, i64 %e
  %r = tail call i32 (ptr, ...) @printf(ptr noundef @g, i32 noundef %add) #3
  %v = select i1 %c, i32 %add, i32 7
  call void @llvm.dbg.declare(metadata ptr %s, metadata !1, metadata !DIExpression())
  %fr = freeze i32 %v
  br i1 %c, label %yes, label %no
yes:
  switch i32 %fr, label %no [
    i32 0, label %no
    i32 5, label %done
  ]
no:
  %m = phi i32 [ %add, %entry ], [ 0, %yes ], [ 0, %yes ]
  br label %done
done:
  %rv = phi i32 [ %m, %no ], [ 5, %yes ]
  ret i32 %rv
}
!7 = !DILocation(line: 42, column: 1, scope: !1)
"""


def test_operation_normalization_of_every_instruction_kind():
    cfg = build_cfgs(parse_ir(OPS_IR))["f"]
    texts = [o.text for n in cfg.nodes for o in n.operations]
    assert texts == [
        "s0 = ALLOC",
        "STORE a0 -> s0",
        "v0 = LOAD s0",
        "v1 = ARITH.add v0, 1",
        "v2 = ARITH.div.u v1, 3",
        "v3 = ARITH.shr.s v2, 1",
        "v4 = CMP.lt.u v3, 10",
        "v5 = CMP.ne.fu a2, 0.000000e+00",
        "v6 = CAST.sext v3",
        "v7 = ADDR a1, 0, v6",
        "v8 = CALL.printf(@g, v1)",
        "v9 = SELECT v4, v1, 7",
        "v10 = OTHER.freeze v9",
        "BRANCH v4 ? B1 : B2",
        "SWITCH v10, default:B2, 0:B2, 5:B3",
        "v11 = PHI v1:B0, 0:B1, 0:B1",
        "JUMP B3",
        "v12 = PHI v11:B2, 5:B1",
        "RETURN v12",
    ]
    assert cfg.node("B0").operations[2].source_line == 42
    assert cfg.node("B0").calls == ["printf"]
    assert cfg.analysis["unsupported"] == ["switch"]
    switch_edges = cfg.out_edges("B1")
    assert [e.kind for e in switch_edges] == ["default", "case:0", "case:5"]
    assert switch_edges[2].branch.taken_when == "v10 == 5" and switch_edges[2].branch.case_value == "5"
    assert cfg.meta["names"]["v10"] == "%fr" and cfg.meta["names"]["B1"] == "%yes"
    assert cfg.check_invariants() == []


def test_schema_rejects_malformed_documents():
    cfg = cfgs_for(REPO / "regression" / "c" / "if.c")[0]["clamp_negative"]
    good = cfg.to_dict()
    validate_json(good)
    for mutate in (
        lambda d: d.pop("edges"),
        lambda d: d["nodes"][0].update(id="entry"),
        lambda d: d["edges"][0].update(kind="maybe"),
        lambda d: d["nodes"][0]["operations"][0].update(op="MOVE"),
        lambda d: d.update(schema="cfgverify.cfg/0"),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(jsonschema.ValidationError):
            validate_json(bad)


def test_invariants_catch_inconsistent_graphs():
    cfg = cfgs_for(REPO / "regression" / "c" / "while.c")[0]["sum_to"]
    assert cfg.check_invariants() == []
    broken = CFG.from_json(cfg.to_json())
    broken.edges.pop()                      # drop the back edge but leave the nodes' successor lists
    assert any("successors" in p for p in broken.check_invariants())
    swapped = CFG.from_json(cfg.to_json())
    t, f = swapped.out_edges("B1")
    t.kind, f.kind = "F", "T"
    assert any("T then F" in p for p in swapped.check_invariants())


def test_networkx_view_has_all_node_and_edge_attributes():
    cfg = cfgs_for(REPO / "regression" / "c" / "break.c")[0]["first_multiple"]
    g = cfg.to_networkx()
    assert set(g.nodes) == {n.id for n in cfg.nodes}
    assert g.nodes["B1"]["label"] == "for.cond" and g.nodes["B1"]["loop_header"] is True
    assert g.nodes["B1"]["operations"][-1]["op"] == "BRANCH"
    data = g.get_edge_data("B1", "B2")["E1"]
    assert data["kind"] == "T" and data["branch"]["predicate"] == "sle"
    assert g.graph["entry"] == "B0" and g.graph["analysis"]["decisions"] == 2


@pytest.mark.parametrize("detail", ["ops", "ir", "none"])
def test_graphviz_rendering(detail, tmp_path):
    cfg = cfgs_for(REPO / "regression" / "c" / "continue.c")[0]["count_nonmultiples"]
    src = cfg_dot.to_dot(cfg, detail).source
    for n in cfg.nodes:
        assert f"\t{n.id} [" in src
    assert src.count("style=dashed") == 2          # the two back edges of this loop
    svg = cfg_dot.render(cfg, tmp_path / "g.svg", detail)
    assert "<svg" in svg.read_text()
    assert cfg_dot.render(cfg, tmp_path / "g.dot", detail).read_text() == src


def test_cli_writes_json_svg_and_gallery(tmp_path):
    files = [str(REPO / "regression" / "c" / "if.c"), str(REPO / "examples" / "c" / "part3" / "gcd.c")]
    assert cli_main(["hll-cfg", *files, "-o", str(tmp_path), "--check"]) == 0
    for name in ("if.clamp_negative", "if.safe_div", "gcd.gcd", "gcd.lcm"):
        doc = (tmp_path / f"{name}.cfg.json").read_text()
        validate_json(CFG.from_json(doc).to_dict())
        assert (tmp_path / f"{name}.svg").is_file()
    page = (tmp_path / "index.html").read_text()
    assert page.count("<section>") == 4 and "gcd.lcm.svg" in page


# ------------------------------------------------------------------------------------- 5. random programs

def _random_file(seed: int, extended: bool) -> tuple:
    prog = random_program(seed, extended=extended)
    path = BUILD / "random" / f"{'ext' if extended else 'sup'}_{seed}.c"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prog.source)
    return prog, path


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_supported_programs(seed):
    prog, c_file = _random_file(seed, extended=False)
    cfgs, ll = cfgs_for(c_file)
    assert all(p == [] for p in check_cfgs(cfgs, ll).values())
    allowed = {"ALLOC", "LOAD", "STORE", "ARITH", "CMP", "JUMP", "BRANCH", "RETURN"}
    for name, cfg in cfgs.items():
        assert cfg.analysis["decisions"] == prog.decisions[name]
        assert len(cfg.analysis["loops"]) == prog.loops[name]
        assert cfg.analysis["unsupported"] == [] and cfg.analysis["unreachable"] == []
        assert {o.op for n in cfg.nodes for o in n.operations} <= allowed
        branch_edges = [e for e in cfg.edges if e.kind in ("T", "F")]
        assert all(e.branch.compare and e.branch.source_text for e in branch_edges)
    o2 = clang(c_file, BUILD / "random" / f"{c_file.stem}.O2.ll", "-O2")
    assert all(p == [] for p in check_cfgs(build_cfgs(read_ir(o2)), o2).values())


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_extended_programs(seed):
    _, c_file = _random_file(seed, extended=True)
    cfgs, ll = cfgs_for(c_file)
    assert all(p == [] for p in check_cfgs(cfgs, ll).values())
    assert not any(o.op == "OTHER" for cfg in cfgs.values() for n in cfg.nodes for o in n.operations)
    o2 = clang(c_file, BUILD / "random" / f"{c_file.stem}.O2.ll", "-O2")
    assert all(p == [] for p in check_cfgs(build_cfgs(read_ir(o2)), o2).values())


@pytest.mark.parametrize("pred,kind", [
    ("eq", "eq"), ("ne", "ne"), ("slt", "lt.s"), ("sle", "le.s"), ("sgt", "gt.s"), ("sge", "ge.s"),
    ("ult", "lt.u"), ("ule", "le.u"), ("ugt", "gt.u"), ("uge", "ge.u"),
])
def test_every_integer_predicate_is_normalized(pred, kind):
    ir = f"define void @p(i32 %a, i32 %b) {{\n  %c = icmp {pred} i32 %a, %b\n  br i1 %c, label %t, label %f\n" \
         f"t:\n  ret void\nf:\n  ret void\n}}\n"
    cfg = build_cfgs(parse_ir(ir))["p"]
    assert cfg.node("B0").operations[0].text == f"v0 = CMP.{kind} a0, a1"
    t, f = cfg.out_edges("B0")
    assert (t.branch.taken_when, f.branch.taken_when) == ("v0 is true", "v0 is false")
    assert t.branch.predicate == pred and t.branch.compare == f"v0 = CMP.{kind} a0, a1"
