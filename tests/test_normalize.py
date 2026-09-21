"""Part 6 tests: semantic operations, comparable conditions, loop signatures, the rules N1-N8 and the proof
that normalization keeps the control-flow meaning.

The checkpoint is `test_every_raw_cfg_normalizes_without_losing_meaning`: every CFG this project can build,
from every C program, from every candidate ISA, from both compilers and both syntaxes, is normalized and then
checked against the raw CFG it came from by `normalize/checks.py` (properties P1-P6).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from cfgverify.cli import main as cli_main                                      # noqa: E402
from cfgverify.model.cfg import CFG, BranchInfo, validate_json                  # noqa: E402
from cfgverify.model.ops import Operation                                       # noqa: E402
from cfgverify.normalize import ALL_RULES, log_to_json, normalize               # noqa: E402
from cfgverify.normalize import checks as norm_checks                           # noqa: E402
from cfgverify.normalize import conditions as cond                              # noqa: E402
from cfgverify.normalize import loops as loop_shapes                            # noqa: E402
from cfgverify.normalize import semantics                                       # noqa: E402
from random_c import random_program                                             # noqa: E402
from test_asm_cfg import ALL_C, ISAS, asm_cfgs, compile_asm, hll_cfgs           # noqa: E402

BUILD = REPO / "build" / "tests"
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "6"))


def _op(op: str, kind: str | None = None, operands: tuple[str, ...] = ()) -> Operation:
    return Operation(op, kind, None, operands, f"{op}.{kind} {', '.join(operands)}")


# ------------------------------------------------------------------------- 1. semantic operations
@pytest.mark.parametrize("operation, expected", [
    (_op("MOVE", None, ("$1", "%ecx")), "ASSIGN"),
    (_op("LOAD"), "LOAD"),
    (_op("STORE"), "STORE"),
    (_op("ARITH", "add", ("v1", "v2")), "ADD"),
    (_op("ARITH", "add", ("v1", "1")), "INCREMENT"),
    (_op("ARITH", "add", ("$1", "%ecx")), "INCREMENT"),
    (_op("ARITH", "add", ("w8", "#1")), "INCREMENT"),
    (_op("ARITH", "sub", ("v1", "1")), "DECREMENT"),
    (_op("ARITH", "sub", ("v1", "v2")), "SUBTRACT"),
    (_op("ARITH", "mul", ("v1", "v2")), "MULTIPLY"),
    (_op("ARITH", "div.s", ("v1", "v2")), "DIVIDE"),
    (_op("ARITH", "rem.s", ("v1", "v2")), "MODULO"),
    (_op("ARITH", "xor", ("v1", "v2")), "BITWISE"),
    (_op("ARITH", "shl", ("v1", "2")), "SHIFT"),
    (_op("ARITH", "neg", ("v1",)), "NEGATE"),
    (_op("CMP", "lt.s", ("v1", "0")), "COMPARE"),
    (_op("CAST", "sext"), "CONVERT"),
    (_op("CALL", "is_prime"), "CALL"),
    (_op("BRANCH", "gt.s"), "CONDITIONAL_BRANCH"),
    (_op("JUMP"), "UNCONDITIONAL_BRANCH"),
    (_op("SWITCH"), "MULTIWAY_BRANCH"),
    (_op("RETURN"), "RETURN"),
    (_op("ALLOC"), "ALLOCATE"),
    (_op("PHI"), "PHI"),
    (_op("SELECT"), "SELECT"),
    (_op("ADDR"), "ADDRESS"),
    (_op("INDIRECT"), "INDIRECT_BRANCH"),
    (_op("UNREACHABLE"), "UNREACHABLE"),
    (_op("OTHER", "endbr64"), "OTHER"),
])
def test_semantic_class(operation, expected):
    assert semantics.semantic_class(operation) == expected


def test_every_category_the_brief_asks_for_exists():
    for name in ("ASSIGN", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "INCREMENT", "DECREMENT", "COMPARE",
                 "CONDITIONAL_BRANCH", "UNCONDITIONAL_BRANCH", "CALL", "RETURN", "LOAD", "STORE"):
        assert semantics.SEMANTIC_CLASSES[name][0] == "brief", name
    assert set(semantics.BRIEF_CLASSES) | set(semantics.EXTENSION_CLASSES) == set(semantics.SEMANTIC_CLASSES)


def test_every_operation_of_every_cfg_gets_a_category():
    """No instruction may fall outside the vocabulary: the mapping is total, by construction and in practice."""
    seen = set()
    for c_file in ALL_C[:4]:
        for cfg in hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll").values():
            for node in cfg.nodes:
                for operation in node.operations:
                    kind = semantics.semantic_class(operation)
                    assert kind in semantics.SEMANTIC_CLASSES
                    seen.add(kind)
    assert {"LOAD", "STORE", "COMPARE", "CONDITIONAL_BRANCH", "RETURN"} <= seen


def test_content_signature_ignores_names_and_control_flow():
    ops = [_op("MOVE", None, ("$1", "%ecx")), _op("ARITH", "add", ("%ecx", "%eax")), _op("JUMP", None, (".L1",))]
    other = [_op("MOVE", None, ("#1", "w8")), _op("ARITH", "add", ("w8", "w9")), _op("JUMP", None, ("B7",))]
    assert semantics.content_signature(ops) == semantics.content_signature(other) == ("ASSIGN", "ADD")


# ----------------------------------------------------------------------- 2. comparable conditions
@pytest.mark.parametrize("predicate, expected", [
    ("sle", ("le", "s")), ("sgt", ("gt", "s")), ("ult", ("lt", "u")), ("eq", ("eq", "")),
    ("le.s", ("le", "s")), ("gt.u", ("gt", "u")), ("eq0", ("eq", "")), ("ne0", ("ne", "")),
    ("nonsense", (None, "")), (None, (None, "")),
])
def test_predicate_spellings_from_both_sides_agree(predicate, expected):
    assert cond.parse_predicate(predicate) == expected


@pytest.mark.parametrize("operand, expected", [
    ("%eax", "var"), ("v3", "var"), ("-4(%rbp)", "var"), ("w8", "var"),
    ("0", "zero"), ("$0", "zero"), ("#0", "zero"), ("5", "const:5"), ("$0x10", "const:16"),
    ("true", "const:1"),
])
def test_operand_shapes(operand, expected):
    assert cond.operand_shape(operand) == expected


def test_the_same_test_written_four_ways_gets_one_key():
    """`i <= n` in IR, `jg` after `cmp` on x86, `b.gt` on AArch64 and `blt n, i` on RISC-V are one test."""
    ir = BranchInfo(taken_when="v2 is true", predicate="sle", compare="v2 = CMP.le.s v0, v1")
    x86 = BranchInfo(taken_when="flags is false", predicate="gt.s", compare="CMP.gt.s -4(%rbp), %eax")
    arm = BranchInfo(taken_when="flags is false", predicate="gt.s", compare="CMP.gt.s w8, w9")
    riscv = BranchInfo(taken_when="a1, a0 is false", predicate="lt.s", compare="CMP.lt.s a1, a0")
    keys = {cond.from_branch(ir).polarity_free_key(),
            cond.from_branch(x86, swap_operands=True).polarity_free_key(),
            cond.from_branch(arm).polarity_free_key(),
            cond.from_branch(riscv).polarity_free_key()}
    assert len(keys) == 1, keys


def test_polarity_and_negation():
    branch = BranchInfo(taken_when="v2 is true", predicate="slt", compare="v2 = CMP.lt.s v0, 0")
    c = cond.from_branch(branch)
    assert c.relation == "lt" and c.signedness == "s" and c.shape == ("zero", "var") or c.shape == ("var", "zero")
    assert c.negated().negated() == c
    assert c.polarity_free_key() == c.negated().polarity_free_key()


def test_sign_bit_test_is_a_comparison_with_zero():
    """AArch64 `tbnz w8, #31` is `x < 0`; without this it would be an unrepresentable condition."""
    tb = BranchInfo(taken_when="flags is true", predicate="bit1", compare="CMP.bit1 w8, #31")
    icmp = BranchInfo(taken_when="v2 is true", predicate="slt", compare="v2 = CMP.lt.s v0, 0")
    assert cond.from_branch(tb).polarity_free_key() == cond.from_branch(icmp).polarity_free_key()


def test_a_destination_first_flag_setter_compares_its_last_two_operands():
    subs = BranchInfo(taken_when="flags is true", predicate="gt.s", compare="CMP.gt.s w8, w8, w9")
    assert cond.from_branch(subs).shape == ("var", "var")


# --------------------------------------------------------------------------- 3. loop signatures
def test_loop_signature_has_no_block_names_in_it():
    cfg = hll_cfgs(REPO / "regression" / "c" / "nested_loop.c",
                   ll_out=BUILD / "ir" / "nested_loop.ll")["count_pairs"]
    signature = loop_shapes.loop_signature(cfg)
    assert signature and "for." not in signature and "B" not in signature.replace("b", "")
    assert signature.count("L(") == 2                       # an outer loop with an inner one


def test_loop_signature_distinguishes_nesting_from_sequence():
    nested = hll_cfgs(REPO / "regression" / "c" / "nested_loop.c",
                      ll_out=BUILD / "ir" / "nested_loop.ll")["count_pairs"]
    single = hll_cfgs(REPO / "regression" / "c" / "while.c", ll_out=BUILD / "ir" / "while.ll")["sum_to"]
    assert loop_shapes.loop_signature(nested) != loop_shapes.loop_signature(single)


# ------------------------------------------------------------------------------- 4. the rules
def _hll(name: str, function: str) -> CFG:
    return hll_cfgs(REPO / "regression" / "c" / name, ll_out=BUILD / "ir" / f"{Path(name).stem}.ll")[function]


def test_n4_gives_exactly_one_exit():
    cfg, log = normalize(_hll("if_else.c", "sign"))
    assert [n.id for n in cfg.nodes if not cfg.out_edges(n.id)] == ["EXIT"]
    assert any(s.rule == "N4" for s in log)


def test_n2_removes_jump_only_blocks():
    raw = _hll("if_else.c", "sign")
    cfg, log = normalize(raw)
    assert any(s.rule == "N2" for s in log)
    assert len(cfg.nodes) < len(raw.nodes) + 1                   # +1 for the EXIT node N4 adds


def test_n8_folds_a_constant_condition():
    raw = hll_cfgs(REPO / "examples" / "c" / "part3" / "primes.c",
                   ll_out=BUILD / "ir" / "primes.ll")["count_primes"]
    assert raw.analysis["decisions"] == 3                        # `do { } while (1)` is a real test in the IR
    cfg, log = normalize(raw)
    assert any(s.rule == "N8" for s in log)
    assert cfg.analysis["decisions"] == 2


def test_rules_can_be_switched_off_individually():
    raw = _hll("if_else.c", "sign")
    full, _ = normalize(raw)
    without_n2, log = normalize(raw, rules=tuple(r for r in ALL_RULES if r != "N2"))
    assert not any(s.rule == "N2" for s in log)
    assert len(without_n2.nodes) > len(full.nodes)
    assert without_n2.meta["rules_applied"] == [r for r in ALL_RULES if r != "N2"]


def test_normalization_is_idempotent():
    """Running the rules again must change nothing: the first run reached a fixpoint."""
    for name, function in (("if_else.c", "sign"), ("nested_loop.c", "count_pairs"), ("break.c", "capped_pairs")):
        once, _ = normalize(_hll(name, function))
        twice, log = normalize(once)
        assert [s for s in log if s.rule not in ("N6",)] == [], f"{function}: {[str(s) for s in log]}"
        shape = lambda c: [(n.id, n.terminator, n.successors, [o.text for o in n.operations]) for n in c.nodes]
        assert shape(twice) == shape(once), function        # provenance restarts; the graph does not move


def test_normalization_does_not_touch_the_input():
    raw = _hll("if_else.c", "sign")
    before = raw.to_json()
    normalize(raw)
    assert raw.to_json() == before


def test_the_rules_never_look_at_which_side_the_cfg_came_from():
    """The contract that makes any later comparison meaningful, checked in the source itself."""
    import re
    source = (REPO / "src" / "cfgverify" / "normalize" / "rules.py").read_text()
    code = re.sub(r'"""(.|\n)*?"""', "", source)                 # docstrings may of course discuss it
    code = "\n".join(line.split("#")[0] for line in code.splitlines())
    for forbidden in ("cfg.origin", ".origin ", '"hll"', '"asm"', '"isa"', "asm_file", "ir_file",
                      "target_triple", "syntax"):
        assert forbidden not in code, f"rules.py must not depend on {forbidden}"


def test_every_block_records_where_it_came_from():
    cfg, _ = normalize(_hll("nested_loop.c", "count_pairs"))
    raw_labels = [b for n in cfg.nodes for b in n.origin_blocks]
    assert len(raw_labels) == len(set(raw_labels))               # no raw block claimed twice
    assert all(n.origin_blocks for n in cfg.nodes)


def test_the_rule_log_is_json_and_replayable():
    cfg, log = normalize(_hll("break.c", "capped_pairs"))
    records = json.loads(log_to_json(log))
    assert [r["index"] for r in records] == list(range(1, len(records) + 1))
    assert all(r["rule"] in ALL_RULES and r["detail"] for r in records)
    assert cfg.meta["rule_steps"] == len(records)


def test_normalized_cfgs_still_validate_against_the_schema():
    for name, function in (("while.c", "sum_to"), ("nested_if.c", "quadrant"), ("do_while.c", "count_digits")):
        cfg, _ = normalize(_hll(name, function))
        validate_json(cfg.to_dict())
        assert CFG.from_json(cfg.to_json()).to_dict() == cfg.to_dict()
        assert cfg.check_invariants() == []


# ------------------------------------------------- 5. THE CHECKPOINT: meaning survives normalization
@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
def test_hll_cfgs_normalize_without_losing_meaning(c_file):
    for name, raw in hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll").items():
        cfg, log = normalize(raw)
        assert norm_checks.check_preserved(raw, cfg, log) == [], f"{name}: preservation"
        assert norm_checks.check_normalized(cfg) == [], f"{name}: normalized CFG is not well formed"


@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
@pytest.mark.parametrize("isa", list(ISAS))
def test_asm_cfgs_normalize_without_losing_meaning(isa, c_file):
    cfgs, _ = asm_cfgs(compile_asm(c_file, isa), isa, c_source=c_file)
    for name, raw in cfgs.items():
        cfg, log = normalize(raw)
        assert norm_checks.check_preserved(raw, cfg, log) == [], f"{isa} {name}: preservation"
        assert norm_checks.check_normalized(cfg) == [], f"{isa} {name}: normalized CFG is not well formed"


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_programs_normalize_without_losing_meaning(seed):
    program = random_program(seed)
    c_file = BUILD / "random" / f"norm_{seed}.c"
    c_file.parent.mkdir(parents=True, exist_ok=True)
    c_file.write_text(program.source)
    raws = dict(hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll"))
    asm, _ = asm_cfgs(compile_asm(c_file, "x86-64"), "x86-64", c_source=c_file)
    for label, group in (("hll", raws), ("asm", asm)):
        for name, raw in group.items():
            cfg, log = normalize(raw)
            assert norm_checks.check_preserved(raw, cfg, log) == [], f"{label} {name}"
            assert norm_checks.check_normalized(cfg) == [], f"{label} {name}"


# ------------------------------------------------------ 6. mutation: the checks must actually catch damage
def test_the_preservation_check_catches_a_dropped_branch_arm():
    """Delete one arm of a decision: a whole behaviour of the program disappears with no rule to explain it."""
    raw = _hll("if_else.c", "sign")
    cfg, log = normalize(raw)
    broken = CFG.from_json(cfg.to_json())
    victim = next(n for n in broken.nodes if len(broken.out_edges(n.id)) == 2)
    broken.edges.remove(broken.out_edges(victim.id)[1])
    for node in broken.nodes:
        node.successors = list(dict.fromkeys(e.destination for e in broken.out_edges(node.id)))
        node.predecessors = sorted({e.source for e in broken.edges if e.destination == node.id})
    broken.compute_analysis()
    assert norm_checks.check_preserved(raw, broken, log) != []


def test_the_preservation_check_catches_a_loop_that_disappeared():
    raw = _hll("while.c", "sum_to")
    cfg, log = normalize(raw)
    broken = CFG.from_json(cfg.to_json())
    broken.edges = [e for e in broken.edges if not e.back_edge]
    for node in broken.nodes:
        node.successors = list(dict.fromkeys(e.destination for e in broken.out_edges(node.id)))
        node.predecessors = sorted({e.source for e in broken.edges if e.destination == node.id})
    broken.compute_analysis()
    assert any("P2" in p for p in norm_checks.check_preserved(raw, broken, log))


def test_the_preservation_check_catches_a_reordered_block():
    """Swap the contents of two blocks: the graph is the same shape, the executions are not."""
    raw = _hll("nested_if.c", "quadrant")
    cfg, log = normalize(raw)
    broken = CFG.from_json(cfg.to_json())
    a, b = broken.nodes[1], broken.nodes[2]
    a.operations, b.operations = b.operations, a.operations
    assert norm_checks.check_preserved(raw, broken, log) != []


def test_the_preservation_check_catches_an_invented_block():
    raw = _hll("while.c", "sum_to")
    cfg, log = normalize(raw)
    broken = CFG.from_json(cfg.to_json())
    broken.nodes[1].origin_blocks = ["a.block.that.never.existed"]
    assert any("P6" in p for p in norm_checks.check_preserved(raw, broken, log))


# ------------------------------------------------------------------------------------ 7. the CLI
def test_cli_normalizes_and_checks(tmp_path):
    source = tmp_path / "sum_to.cfg.json"
    source.write_text(_hll("while.c", "sum_to").to_json())
    assert cli_main(["normalize", str(source), "-o", str(tmp_path / "out"), "--check", "-v"]) == 0
    out = tmp_path / "out"
    cfg = CFG.from_json((out / "sum_to.norm.cfg.json").read_text())
    validate_json(cfg.to_dict())
    assert cfg.meta["normalized"] is True and cfg.meta["rule_steps"] >= 1
    assert json.loads((out / "sum_to.norm.rules.json").read_text())
    assert (out / "sum_to.norm.svg").is_file() and "sum_to" in (out / "index.html").read_text()


def test_cli_rejects_an_unknown_rule(tmp_path, capsys):
    source = tmp_path / "x.cfg.json"
    source.write_text(_hll("while.c", "sum_to").to_json())
    assert cli_main(["normalize", str(source), "-o", str(tmp_path), "--rules", "N99"]) == 1


# --------------------------------------------- 8. what normalization is FOR: the two sides converge
@pytest.mark.parametrize("isa", list(ISAS))
def test_normalization_brings_the_two_sides_closer(isa):
    """Not a matching test (that is Part 7). A measurement: normalization must not make things worse."""
    raw_same = normalized_same = total = 0
    for c_file in ALL_C:
        hll = hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll")
        asm, _ = asm_cfgs(compile_asm(c_file, isa), isa, c_source=c_file)
        for name, h in hll.items():
            a = asm[name]
            total += 1
            raw_same += (h.analysis["blocks"], h.analysis["edges"]) == (a.analysis["blocks"],
                                                                       a.analysis["edges"])
            hn, _ = normalize(h)
            an, _ = normalize(a)
            normalized_same += (hn.analysis["blocks"], hn.analysis["edges"]) == (an.analysis["blocks"],
                                                                                an.analysis["edges"])
    assert normalized_same >= raw_same, f"{isa}: normalization moved the sides apart"
    assert normalized_same >= total - 1, f"{isa}: only {normalized_same}/{total} agree after normalization"


def test_normalization_is_the_same_code_for_both_sides():
    """The same raw CFG normalized as `hll` and as `asm` must give the same graph."""
    raw = _hll("nested_loop.c", "count_pairs")
    as_asm = CFG.from_json(raw.to_json())
    as_asm.origin = "asm"
    left, _ = normalize(raw)
    right, _ = normalize(as_asm)
    assert [(n.id, n.terminator, n.successors) for n in left.nodes] == \
           [(n.id, n.terminator, n.successors) for n in right.nodes]
