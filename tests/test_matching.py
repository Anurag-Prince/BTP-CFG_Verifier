"""Part 7 tests: the matcher, the findings, the verdict.

Two things have to be shown, and the second is the harder one:

1. **Correct assembly matches its C program.** Every regression and example program, compiled for every
   candidate ISA, must come out MATCH — with one documented exception (`count_primes`, Part 6 finding F2).
2. **Broken assembly is caught, and the finding says where.** A matcher that answers MATCH for everything
   would pass (1) perfectly. So the assembly is deliberately damaged — a branch target swapped, a jump
   deleted, a `ret` removed, a loop's back edge cut — and each mutation must produce the *specific* finding
   kind the brief names, not merely "MISMATCH".

The checkpoint is `test_the_same_matcher_works_for_every_program`: one matcher, no program-specific rules,
asserted by reading the source as well as by running it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from cfgverify.cli import main as cli_main                                        # noqa: E402
from cfgverify.match import matcher, verify                                       # noqa: E402
from cfgverify.match import invariants                                            # noqa: E402
from cfgverify.model.evidence import BRIEF_KINDS, FINDING_KINDS, Verdict          # noqa: E402
from cfgverify.normalize import normalize                                         # noqa: E402
from cfgverify.report import render, verdict as verdict_module                    # noqa: E402
from random_c import random_program                                               # noqa: E402
from test_asm_cfg import ALL_C, ISAS, asm_cfgs, compile_asm, hll_cfgs             # noqa: E402

BUILD = REPO / "build" / "tests"
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "6"))

#: Part 6 finding F2: Clang keeps `do { } while (1)` as a branch on a register holding 1, so the assembly has
#: a decision the IR folded away. The shapes genuinely differ; a MATCH here would be the bug.
KNOWN_SHAPE_DIFFERENCE = {"count_primes"}


def _pair(c_file: Path, isa: str, asm_path: Path | None = None):
    hll = hll_cfgs(c_file, ll_out=BUILD / "ir" / f"{c_file.stem}.ll")
    asm, _ = asm_cfgs(asm_path or compile_asm(c_file, isa), isa, c_source=c_file)
    return hll, asm


# ------------------------------------------------------------- 1. correct assembly matches its C program
@pytest.mark.parametrize("c_file", ALL_C, ids=[p.name for p in ALL_C])
@pytest.mark.parametrize("isa", list(ISAS))
def test_correct_assembly_matches(isa, c_file):
    hll, asm = _pair(c_file, isa)
    report = verify(hll, asm)
    for result in report.functions:
        if result.function in KNOWN_SHAPE_DIFFERENCE:
            continue
        assert result.verdict == "MATCH", \
            f"{isa} {c_file.name} {result.function}: {[str(f) for f in result.findings]}"
        assert result.mapping and result.mapping.complete and result.mapping.method == "isomorphism"


def test_the_known_shape_difference_is_reported_not_hidden():
    """`count_primes` must come out MISMATCH, and the findings must point at the extra decision."""
    hll, asm = _pair(REPO / "examples" / "c" / "part3" / "primes.c", "x86-64")
    report = verify({"count_primes": hll["count_primes"]}, {"count_primes": asm["count_primes"]})
    result = report.functions[0]
    assert result.verdict == "MISMATCH"
    assert result.asm_summary["decisions"] > result.hll_summary["decisions"]
    assert {"EXTRA_BLOCK", "STRUCTURAL_MISMATCH"} & {f.kind for f in result.findings}


# --------------------------------------------------------------------- 2. mutations: each finding kind
def _mutate(c_file: Path, isa: str, mutate, tag: str) -> tuple[dict, dict]:
    """Compile the program, damage the assembly TEXT, and rebuild the CFGs from the damaged file."""
    good = Path(compile_asm(c_file, isa))
    text = mutate(good.read_text())
    broken = BUILD / "mutants" / f"{c_file.stem}.{isa}.{tag}.s"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(text)
    return _pair(c_file, isa, asm_path=broken)


def _kinds(report) -> set[str]:
    return {f.kind for f in report.all_findings()}


def _swap_first_branch_targets(text: str) -> str:
    """Send a conditional branch to the other arm: the classic 'wrong branch' bug."""
    labels = re.findall(r"^\.LBB\d+_\d+:", text, re.M)
    assert len(labels) >= 2, "need two machine block labels to swap"
    first, second = labels[0][:-1], labels[1][:-1]
    out, swapped = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if not swapped and stripped.endswith(first) and not stripped.startswith("."):
            out.append(line.replace(first, second))
            swapped = True
        else:
            out.append(line)
    assert swapped, "no branch to that label"
    return "\n".join(out) + "\n"


def _delete_a_meaningful_jump(text: str) -> str:
    """Delete a `jmp` whose target is NOT the label that follows it.

    Deleting a jump to the very next label changes nothing — control falls through to the same place — and
    the matcher is right to answer MATCH for that. A mutation test has to damage the program, not merely
    edit the file.
    """
    lines = text.splitlines()
    next_label = {}
    for index, line in enumerate(lines):
        m = re.match(r"^(\.LBB\d+_\d+):", line.strip())
        if m:
            for earlier in range(index - 1, -1, -1):
                next_label.setdefault(earlier, m.group(1))
                if re.match(r"^\.LBB\d+_\d+:", lines[earlier].strip()):
                    break
    out, done = [], False
    for index, line in enumerate(lines):
        target = re.match(r"^(?:jmp|b|j)\s+(\.LBB\d+_\d+)$", line.strip())
        if not done and target and next_label.get(index) != target.group(1):
            done = True
            continue
        out.append(line)
    assert done, "no jump whose target is not the next block"
    return "\n".join(out) + "\n"


def _delete_a_return(text: str) -> str:
    out, done = [], False
    for line in text.splitlines():
        if not done and line.strip() in ("retq", "ret"):
            done = True
            continue
        out.append(line)
    assert done, "no return to delete"
    return "\n".join(out) + "\n"


def _break_a_back_edge(text: str) -> str:
    """Redirect a loop's backward jump to the block that follows it: that loop stops being a loop.

    Applied to a *nested* loop and to the inner back edge, so the outer loop survives and the graphs stay
    close enough to map — which is the whole point. Breaking the only loop of a single-loop function
    collapses the CFG so far that normalization merges the blocks away, and the interesting finding
    (a back edge with no counterpart) has nothing left to be about.
    """
    lines = text.splitlines()
    seen: set[str] = set()
    out, done = [], False
    for index, line in enumerate(lines):
        stripped = line.strip()
        label = re.match(r"^(\.LBB\d+_\d+):", stripped)
        if label:
            seen.add(label.group(1))
        target = re.match(r"^(?:jmp|b|j)\s+(\.LBB\d+_\d+)$", stripped)
        if not done and target and target.group(1) in seen:            # a jump backwards = a back edge
            following = next((re.match(r"^(\.LBB\d+_\d+):", lines[j].strip())
                              for j in range(index + 1, len(lines))
                              if re.match(r"^\.LBB\d+_\d+:", lines[j].strip())), None)
            assert following, "no block after the back edge to redirect it to"
            out.append(line.replace(target.group(1), following.group(1)))
            done = True
            continue
        out.append(line)
    assert done, "no back edge found"
    return "\n".join(out) + "\n"


def _change_the_test(text: str) -> str:
    """Keep the shape, change what is tested: an ordered comparison becomes an equality test.

    Note what this mutation deliberately is *not*: changing `jg` to `jge`. Part 6 finding F4 says that with
    two `var` operands and unknown arm order, `lt`/`le`/`gt`/`ge` collapse into one comparable key, so that
    edit is invisible to L4 by design and the matcher answers MATCH — correctly, for what it can see.
    Distinguishing it needs the value mapping of a future L4+, which is **[C1]**.
    """
    swaps = [(r"\bjle\b", "jne"), (r"\bjge\b", "jne"), (r"\bjg\b", "jne"), (r"\bjl\b", "jne"),
             (r"\bb\.le\b", "b.ne"), (r"\bb\.ge\b", "b.ne"), (r"\bb\.gt\b", "b.ne"),
             (r"\bble\b", "bne"), (r"\bbge\b", "bne"), (r"\bblt\b", "bne")]
    for pattern, replacement in swaps:
        new = re.sub(pattern, replacement, text, count=1)
        if new != text:
            return new
    pytest.skip("no comparison to change in this file")


def _rename_a_function(text: str) -> str:
    return re.sub(r"\bsum_to\b", "sum_to_renamed", text)


#: Each mutation, and the finding kind(s) it must produce. A mutation may legitimately produce *more* than
#: one kind — breaking a branch target moves an edge, which is both a missing edge and an extra one — so the
#: assertion is that the named kind is among them, and that the damage is caught at all.
MUTATIONS = [
    # (id, C file, ISA, mutation, the kind it must produce)
    ("swapped-branch-targets", "if_else.c", "x86-64", _swap_first_branch_targets, "WRONG_BRANCH"),
    ("deleted-jump", "nested_if.c", "x86-64", _delete_a_meaningful_jump, "MISSING_EDGE"),
    ("deleted-return", "if.c", "x86-64", _delete_a_return, "UNSUPPORTED_CONSTRUCT"),
    ("broken-back-edge", "nested_loop.c", "x86-64", _break_a_back_edge, "MISSING_LOOP_BACK_EDGE"),
    ("changed-test", "while.c", "x86-64", _change_the_test, "WRONG_BRANCH_CONDITION"),
    ("renamed-function", "while.c", "x86-64", _rename_a_function, "FUNCTION_MISSING"),
]


@pytest.mark.parametrize("tag, program, isa, mutation, expected",
                         MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_a_mutation_produces_its_finding(tag, program, isa, mutation, expected):
    c_file = REPO / "regression" / "c" / program
    hll, asm = _mutate(c_file, isa, mutation, tag)
    report = verify(hll, asm, require_conditions=(expected == "WRONG_BRANCH_CONDITION"))
    assert expected in _kinds(report), \
        f"{tag}: expected {expected}, got {sorted(_kinds(report))}"
    assert report.verdict in ("MISMATCH", "UNSUPPORTED"), f"{tag} was not caught at all"


def test_a_semantically_harmless_edit_is_not_reported_as_a_mismatch():
    """Deleting a `jmp` to the very next block changes nothing, and must still be MATCH.

    Without this, a matcher could pass every mutation test by calling everything a mismatch.
    """
    def delete_a_redundant_jump(text: str) -> str:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            target = re.match(r"^jmp\s+(\.LBB\d+_\d+)$", line.strip())
            if not target:
                continue
            following = next((lines[j].strip() for j in range(index + 1, len(lines))
                              if lines[j].strip().startswith(".LBB")), "")
            if following.startswith(target.group(1) + ":"):
                return "\n".join(lines[:index] + lines[index + 1:]) + "\n"
        pytest.skip("no redundant jump in this file")

    hll, asm = _mutate(REPO / "regression" / "c" / "nested_if.c", "x86-64",
                       delete_a_redundant_jump, "redundant-jump")
    report = verify(hll, asm)
    assert report.verdict == "MATCH", [str(f) for f in report.all_findings()]


def test_every_finding_kind_the_brief_asks_for_can_be_produced():
    """All ten, from real inputs — no hand-built CFGs, no fabricated findings."""
    produced: set[str] = set()

    for tag, program, isa, mutation, _ in MUTATIONS:
        hll, asm = _mutate(REPO / "regression" / "c" / program, isa, mutation, tag)
        produced |= _kinds(verify(hll, asm, require_conditions=True))

    # MISSING_BLOCK / EXTRA_BLOCK / EXTRA_EDGE / UNEXPECTED_PATH: compare a program with a DIFFERENT
    # program's assembly, renamed so the functions pair up. Nothing could be more "not the same program".
    left = REPO / "regression" / "c" / "while.c"
    right = REPO / "regression" / "c" / "nested_loop.c"
    hll, _ = _pair(left, "x86-64")
    _, other = _pair(right, "x86-64")
    produced |= _kinds(verify({"sum_to": hll["sum_to"]}, {"sum_to": other["count_pairs"]}))

    produced |= _kinds(verify({}, {"stray": _pair(left, "x86-64")[1]["sum_to"]}))   # FUNCTION_EXTRA

    # STRUCTURAL_MISMATCH: two programs whose block and edge counts happen to line up but whose wiring
    # does not.
    for program in ("if_else.c", "nested_if.c", "break.c", "continue.c", "gcd.c"):
        path = (REPO / "regression" / "c" / program)
        if not path.exists():
            path = REPO / "examples" / "c" / "part3" / program
        hll_a, _ = _pair(left, "x86-64")
        _, asm_b = _pair(path, "x86-64")
        for name, cfg in asm_b.items():
            produced |= _kinds(verify({"sum_to": hll_a["sum_to"]}, {"sum_to": cfg}))

    missing = set(BRIEF_KINDS) - produced
    assert not missing, f"never produced: {sorted(missing)}"


# ------------------------------------------------------------------------------ 3. the verdict policy
def test_a_match_needs_a_positive_result_not_just_silence():
    """MATCH requires an isomorphism with a complete mapping — never merely the absence of findings."""
    result = verdict_module.decide_function("f", [], None, None, {}, {})
    assert result.verdict == "MISMATCH"


def test_condition_disagreements_are_warnings_unless_asked_otherwise():
    c_file = REPO / "regression" / "c" / "while.c"
    hll, asm = _mutate(c_file, "x86-64", _change_the_test, "changed-test")
    lenient = verify(hll, asm)
    strict = verify(hll, asm, require_conditions=True)
    assert "WRONG_BRANCH_CONDITION" in _kinds(lenient)
    assert all(f.severity == "warning" for f in lenient.all_findings()
               if f.kind == "WRONG_BRANCH_CONDITION")
    assert all(f.severity == "error" for f in strict.all_findings()
               if f.kind == "WRONG_BRANCH_CONDITION")
    assert lenient.functions[0].verdict == "MATCH" and strict.functions[0].verdict == "MISMATCH"


def test_unsupported_can_be_turned_off():
    from cfgverify.model.evidence import new_finding
    finding = new_finding([0], "UNSUPPORTED_CONSTRUCT", "warning", "f", "indirect jump")
    assert verdict_module.decide_function("f", [finding], None, None, {}, {}).verdict == "UNSUPPORTED"
    assert verdict_module.decide_function("f", [finding], None, None, {}, {},
                                          allow_unsupported=False).verdict == "MISMATCH"


def test_only_verdict_py_creates_a_verdict():
    """Rule R4, checked in the source: no other module may construct a Verdict."""
    for path in (REPO / "src" / "cfgverify").rglob("*.py"):
        if path.name in ("verdict.py", "evidence.py"):
            continue
        source = path.read_text()
        assert not re.search(r"\bVerdict\(", source), f"{path} constructs a Verdict directly"


# -------------------------------------------------------- 4. THE CHECKPOINT: no program-specific rules
def test_the_same_matcher_works_for_every_program():
    """One matcher, every supported program, every candidate ISA, no per-program anything."""
    matched = total = 0
    for isa in ISAS:
        for c_file in ALL_C:
            hll, asm = _pair(c_file, isa)
            for result in verify(hll, asm).functions:
                total += 1
                matched += result.verdict == "MATCH"
    expected_exceptions = len(ISAS) * len(KNOWN_SHAPE_DIFFERENCE)
    assert matched == total - expected_exceptions, f"{matched}/{total} matched"


def test_the_matcher_contains_no_program_specific_rule():
    """Read the source: no function name, file name or ISA name may appear in the matching logic."""
    keywords = {"for", "while", "if", "break", "continue", "do_while", "sequence", "if_else", "nested_if",
                "nested_loop"}                      # C keywords: a file called for.c proves nothing
    names = {p.stem for p in (REPO / "regression" / "c").glob("*.c")} - keywords
    names |= {p.stem for p in (REPO / "examples" / "c" / "part3").glob("*.c")} - keywords
    names |= {"sum_to", "factorial", "gcd", "quadrant", "sign", "max2", "bubble_sort", "binary_search",
              "is_prime", "count_pairs", "collatz_steps", "count_primes", "clamp_negative", "safe_div"}
    names |= {"x86-64", "aarch64", "riscv", "x86_64", "\"att\"", "\"intel\"", "LBB", ".loc"}
    for module in ("matcher.py", "invariants.py", "diagnose.py"):
        source = (REPO / "src" / "cfgverify" / "match" / module).read_text()
        code = re.sub(r'"""(.|\n)*?"""', "", source)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        for name in names:
            # Word boundaries: `sign` is a function in if_else.c and also lives inside `signature` and
            # `signedness`, which are the matcher's own vocabulary.
            assert not re.search(rf"\b{re.escape(name)}\b", code), f"{module} mentions {name!r}"


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_random_programs_match_their_own_assembly(seed):
    program = random_program(seed)
    c_file = BUILD / "random" / f"match_{seed}.c"
    c_file.parent.mkdir(parents=True, exist_ok=True)
    c_file.write_text(program.source)
    hll, asm = _pair(c_file, "x86-64")
    report = verify(hll, asm)
    bad = [r for r in report.functions if r.verdict != "MATCH"]
    assert not bad, f"seed {seed}: " + "; ".join(f"{r.function} {[str(f) for f in r.findings][:2]}" for r in bad)


# --------------------------------------------------------------------- 5. determinism, evidence, CLI
def test_the_result_is_deterministic():
    hll, asm = _pair(REPO / "regression" / "c" / "nested_loop.c", "aarch64")
    first, second = verify(hll, asm), verify(hll, asm)
    assert first.to_json() == second.to_json()


def test_the_evidence_record_round_trips():
    hll, asm = _pair(REPO / "regression" / "c" / "while.c", "x86-64")
    report = verify(hll, asm)
    assert Verdict.from_json(report.to_json()).to_dict() == report.to_dict()


def test_every_finding_names_where_it_is():
    hll, asm = _mutate(REPO / "regression" / "c" / "if_else.c", "x86-64",
                       _swap_first_branch_targets, "swapped-branch-targets")
    for finding in verify(hll, asm).all_findings():
        assert finding.description and finding.id and finding.function
        if finding.kind not in ("UNEXPECTED_PATH", "STRUCTURAL_MISMATCH", "FUNCTION_MISSING",
                                "FUNCTION_EXTRA", "AMBIGUOUS_MAPPING"):
            assert finding.hll_blocks or finding.asm_blocks, f"{finding.kind} says nothing about where"


def test_finding_kinds_are_documented():
    for kind in BRIEF_KINDS:
        assert FINDING_KINDS[kind][0] == "brief" and FINDING_KINDS[kind][1]
    assert len(BRIEF_KINDS) == 10


def test_matcher_levels_can_be_limited():
    hll, asm = _pair(REPO / "regression" / "c" / "while.c", "x86-64")
    left, right = normalize(hll["sum_to"])[0], normalize(asm["sum_to"])[0]
    assert matcher.match(left, right, level="L1").levels == {"L1": "agree"}
    assert "L4" not in matcher.match(left, right, level="L3").levels
    assert matcher.match(left, right, level="L4").isomorphic


def test_invariant_fingerprints_ignore_names():
    hll, asm = _pair(REPO / "regression" / "c" / "while.c", "aarch64")
    left, right = normalize(hll["sum_to"])[0], normalize(asm["sum_to"])[0]
    assert invariants.disagreements(invariants.fingerprint(left), invariants.fingerprint(right)) == []


def test_cli_verify_reports_and_exits(tmp_path):
    c_file = REPO / "regression" / "c" / "nested_loop.c"
    asm = compile_asm(c_file, "riscv")
    assert cli_main(["verify", str(c_file), str(asm), "--isa", "riscv", "-o", str(tmp_path)]) == 0
    report = Verdict.from_json((tmp_path / "nested_loop.verdict.json").read_text())
    assert report.verdict == "MATCH"
    markdown = (tmp_path / "nested_loop.md").read_text()
    assert "**Verdict: MATCH**" in markdown and "count_pairs" in markdown


def test_cli_verify_exit_code_says_mismatch(tmp_path):
    c_file = REPO / "regression" / "c" / "nested_loop.c"
    hll, asm = _mutate(c_file, "x86-64", _break_a_back_edge, "broken-back-edge")
    broken = BUILD / "mutants" / "nested_loop.x86-64.broken-back-edge.s"
    assert cli_main(["verify", str(c_file), str(broken), "--isa", "x86-64", "-o", str(tmp_path)]) == 2
    assert "MISSING_LOOP_BACK_EDGE" in (tmp_path / "nested_loop.md").read_text()


def test_the_markdown_report_explains_how_it_decided():
    hll, asm = _pair(REPO / "regression" / "c" / "while.c", "x86-64")
    text = render.markdown(verify(hll, asm))
    assert "How this was decided" in text and "deterministic" in text
    assert "**Verdict: MATCH**" in text
