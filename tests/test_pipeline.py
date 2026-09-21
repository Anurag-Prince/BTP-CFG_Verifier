"""Part 8 tests: the system takes general input, and is not built around the regression programs.

The requirement is a negative one — *adding a new supported program must not require modifying the CFG
generator, the normalizer or the matcher* — so these tests are written to be able to fail:

* programs written after the implementation was frozen are verified without touching it
  (`test_unseen_programs_verify`), on every candidate architecture;
* the implementation is read and must not contain any program or function name from the repo
  (`test_no_program_name_appears_in_the_implementation`);
* the implementation's checksums are taken before and after running unseen inputs
  (`test_running_unseen_programs_changes_no_implementation_file`);
* the explanation layer may never change a verdict, and its post-check must reject text that tries
  (`test_a_model_answer_that_contradicts_the_verdict_is_rejected`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

from cfgverify.explain import explainer                                  # noqa: E402
from cfgverify.model.evidence import Verdict                             # noqa: E402
from cfgverify.pipeline import STEPS, PipelineError, find_pairs, run     # noqa: E402
from random_c import random_program                                      # noqa: E402
from test_asm_cfg import ISAS, compile_asm                      # noqa: E402

import main as entry_point                                               # noqa: E402

BUILD = REPO / "build" / "tests" / "part8"
UNSEEN = sorted((REPO / "examples" / "c" / "unseen").glob("*.c"))
FUZZ_N = int(os.environ.get("CFGVERIFY_FUZZ_N", "6"))

#: Every file that would have to change if the system were hard-coded for particular programs.
IMPLEMENTATION = [
    "hll/ir_reader.py", "hll/ir_ops.py", "hll/cfg_builder.py",
    "asm/reader.py", "asm/cfg_builder.py",
    "asm/isa/base.py", "asm/isa/x86.py", "asm/isa/aarch64.py", "asm/isa/riscv.py",
    "normalize/semantics.py", "normalize/conditions.py", "normalize/loops.py", "normalize/rules.py",
    "match/invariants.py", "match/matcher.py", "match/diagnose.py",
    "report/verdict.py", "pipeline.py",
]


def _checksums() -> dict[str, str]:
    return {name: hashlib.sha256((REPO / "src" / "cfgverify" / name).read_bytes()).hexdigest()
            for name in IMPLEMENTATION}


def _verify(c_file: Path, isa: str = "x86-64", **kwargs):
    asm = compile_asm(c_file, isa)
    return run(c_file, [asm], isa=isa, out_dir=BUILD / isa / c_file.stem, **kwargs)


# ------------------------------------------------------------- 1. the pipeline runs every step, in order
def test_the_pipeline_declares_the_steps_the_brief_lists():
    assert len(STEPS) == 8
    joined = " | ".join(STEPS).lower()
    for phrase in ("source", "llvm ir", "basic blocks", "hll cfg", "assembly", "normalize", "match"):
        assert phrase in joined, phrase


def test_every_intermediate_artefact_is_written_down():
    result = _verify(UNSEEN[0])
    out = BUILD / "x86-64" / UNSEEN[0].stem
    assert (out / f"{UNSEEN[0].stem}.ll").is_file()
    for function in result.hll:
        for suffix in ("hll.cfg.json", "hll.norm.cfg.json", "hll.rules.json",
                       "asm.cfg.json", "asm.norm.cfg.json", "asm.rules.json"):
            assert (out / f"{function}.{suffix}").is_file(), f"{function}.{suffix}"
    assert result.artifacts["markdown"].is_file() and result.artifacts["json"].is_file()
    assert Verdict.from_json(result.artifacts["json"].read_text()).verdict == result.verdict.verdict


def test_a_failing_step_says_which_step_failed(tmp_path):
    missing = tmp_path / "nothing.c"
    with pytest.raises(PipelineError) as caught:
        run(missing, [tmp_path / "nothing.s"], isa="x86-64", out_dir=tmp_path)
    assert caught.value.step == STEPS[0]

    source = tmp_path / "p.c"
    source.write_text("int f(int x) { return x; }\n")
    with pytest.raises(PipelineError) as caught:
        run(source, [tmp_path / "absent.s"], isa="x86-64", out_dir=tmp_path)
    assert caught.value.step == STEPS[4]


# ------------------------------------------------------ 2. THE CHECKPOINT: unseen programs, same pipeline
@pytest.mark.parametrize("c_file", UNSEEN, ids=[p.stem for p in UNSEEN])
@pytest.mark.parametrize("isa", list(ISAS))
def test_unseen_programs_verify(isa, c_file):
    """Programs written after the implementation was finished. Nothing was changed to make them work."""
    result = _verify(c_file, isa)
    assert result.verdict.verdict == "MATCH", \
        f"{isa} {c_file.name}: {[str(f) for f in result.verdict.all_findings()]}"
    assert all(r.mapping and r.mapping.complete for r in result.verdict.functions)


def test_the_unseen_corpus_uses_constructs_the_regression_set_does_not():
    """If the new programs were just copies of the old ones, verifying them would prove nothing."""
    text = "\n".join(p.read_text() for p in UNSEEN)
    assert "return" in text and text.count("for (") >= 4
    assert re.search(r"for\s*\(.*?for\s*\(.*?for\s*\(", text, re.S), "no three-deep loop nesting"
    assert re.search(r"do\s*\{[^}]*continue", text, re.S), "no continue inside a do-while"
    assert re.search(r"\bfib\s*\(\s*n\s*-\s*1\s*\)", text), "no recursion"
    regression = "\n".join(p.read_text() for p in (REPO / "regression" / "c").glob("*.c"))
    for program in UNSEEN:
        assert program.read_text() not in regression


@pytest.mark.parametrize("seed", range(FUZZ_N))
def test_freshly_generated_programs_verify(seed):
    """Programs nobody wrote at all, generated at test time."""
    c_file = BUILD / "generated" / f"p_{seed}.c"
    c_file.parent.mkdir(parents=True, exist_ok=True)
    c_file.write_text(random_program(seed + 9000).source)
    result = _verify(c_file)
    assert result.verdict.verdict == "MATCH", \
        f"seed {seed}: {[str(f) for f in result.verdict.all_findings()]}"


def test_running_unseen_programs_changes_no_implementation_file():
    """The requirement, stated as a test: the implementation is byte-identical afterwards."""
    before = _checksums()
    for c_file in UNSEEN:
        _verify(c_file)
    assert _checksums() == before


def test_no_program_name_appears_in_the_implementation():
    names: set[str] = set()
    for folder in ("regression/c", "examples/c/part3", "examples/c/unseen"):
        for path in (REPO / folder).glob("*.c"):
            names.add(path.stem)
            names |= set(re.findall(r"^\w[\w ]*?\b(\w+)\s*\(", path.read_text(), re.M))
    # C keywords, and words that are also part of the implementation's own vocabulary: `sign` is a
    # function in if_else.c and also an x86 condition-code name; `mix` is both a function and an English word.
    names -= {"for", "while", "if", "else", "do", "switch", "return", "int", "void", "char", "main",
              "sizeof", "break", "continue", "sequence", "straight", "search", "ranges", "digits",
              "sign", "mix", "classify"}
    assert len(names) > 25, "the name list is too small to prove anything"

    for name in IMPLEMENTATION:
        source = (REPO / "src" / "cfgverify" / name).read_text()
        code = re.sub(r'"""(.|\n)*?"""', "", source)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        for token in names:
            assert not re.search(rf"\b{re.escape(token)}\b", code), f"{name} mentions {token!r}"


# ------------------------------------------------------------------- 3. the command line the brief names
def test_the_documented_command_works(tmp_path, capsys):
    c_file = UNSEEN[1]
    asm = compile_asm(c_file, "x86-64")
    code = entry_point.main(["--source", str(c_file), "--assembly", str(asm),
                             "--isa", "x86-64", "-o", str(tmp_path)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "VERDICT: MATCH" in printed


def test_the_architecture_is_detected_and_announced(tmp_path, capsys):
    c_file = UNSEEN[0]
    asm = compile_asm(c_file, "aarch64")
    assert entry_point.main(["--source", str(c_file), "--assembly", str(asm), "-o", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "aarch64" in printed and "TO CONFIRM WITH PROFESSOR" in printed


def test_batch_mode_pairs_by_stem(tmp_path, capsys):
    for c_file in UNSEEN[:3]:
        (tmp_path / c_file.name).write_text(c_file.read_text())
        subprocess.run(["clang", "-S", "-O0", "-g", "-fno-discard-value-names",
                        "-o", str(tmp_path / f"{c_file.stem}.s"), str(c_file)], check=True)
    assert len(find_pairs(tmp_path)) == 3
    assert entry_point.main(["--batch", str(tmp_path), "--isa", "x86-64",
                             "-o", str(tmp_path / "out")]) == 0
    assert "3/3 MATCH" in capsys.readouterr().out


def test_json_output_is_the_evidence_record(tmp_path, capsys):
    c_file = UNSEEN[3]
    asm = compile_asm(c_file, "x86-64")
    entry_point.main(["--source", str(c_file), "--assembly", str(asm), "--isa", "x86-64",
                      "--json", "-o", str(tmp_path)])
    report = Verdict.from_json(capsys.readouterr().out)
    assert report.verdict == "MATCH" and report.functions


def test_exit_codes(tmp_path):
    c_file = REPO / "regression" / "c" / "while.c"
    good = compile_asm(c_file, "x86-64")
    assert entry_point.main(["--source", str(c_file), "--assembly", str(good), "--isa", "x86-64",
                             "-o", str(tmp_path), "-q"]) == 0
    broken = tmp_path / "broken.s"
    broken.write_text(Path(good).read_text().replace("sum_to", "renamed_function"))
    assert entry_point.main(["--source", str(c_file), "--assembly", str(broken), "--isa", "x86-64",
                             "-o", str(tmp_path), "-q"]) == 2
    assert entry_point.main(["--source", str(c_file), "--assembly", str(tmp_path / "absent.s"),
                             "--isa", "x86-64", "-o", str(tmp_path), "-q"]) == 1


def test_steps_can_be_listed(capsys):
    assert entry_point.main(["--steps"]) == 0
    printed = capsys.readouterr().out
    assert all(step in printed for step in STEPS)


# ------------------------------------------------------------------- 4. the explanation, and its limits
def test_the_deterministic_explanation_needs_no_model():
    result = _verify(UNSEEN[2], explain=True)
    assert result.explanation and "Explanation" in result.explanation
    assert "not by a language model" in result.explanation


def test_every_finding_appears_in_the_explanation():
    c_file = REPO / "regression" / "c" / "while.c"
    asm = Path(compile_asm(c_file, "x86-64"))
    broken = BUILD / "broken.s"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text(asm.read_text().replace("sum_to", "other_name"))
    result = run(c_file, [broken], isa="x86-64", out_dir=BUILD / "broken", explain=True)
    assert result.verdict.verdict == "MISMATCH"
    for finding in result.verdict.all_findings():
        assert finding.id in result.explanation


def test_a_model_answer_that_contradicts_the_verdict_is_rejected():
    result = _verify(UNSEEN[0])
    report = result.verdict
    assert report.verdict == "MATCH"

    for bad in ("The programs MISMATCH badly.",
                "See finding F99 for details.",
                "There is a MISSING_BLOCK in the assembly.",
                ""):
        assert explainer.check(bad, report), f"accepted a bad answer: {bad!r}"
        answer = explainer.model(report, provider=lambda _prompt, text=bad: text)
        assert answer.source == "template" and answer.rejected


def test_a_good_model_answer_is_framed_with_the_deterministic_verdict():
    report = _verify(UNSEEN[0]).verdict
    good = "The two control-flow graphs have the same shape, so the assembly follows the C program."
    assert explainer.check(good, report) == []
    answer = explainer.model(report, provider=lambda _prompt: good)
    assert answer.source == "model"
    assert "Deterministic verdict: MATCH" in answer.text and "non-authoritative" in answer.text


def test_a_provider_that_crashes_falls_back_silently():
    report = _verify(UNSEEN[0]).verdict

    def provider(_prompt):
        raise RuntimeError("no network")

    answer = explainer.model(report, provider=provider)
    assert answer.source == "template" and "no network" in answer.rejected[0]


def test_the_prompt_contains_only_the_evidence():
    report = _verify(UNSEEN[0]).verdict
    prompt = explainer.build_prompt(report)
    assert "MATCH" in prompt and "may not change it" in prompt
    assert "/home/" not in prompt and ".venv" not in prompt


def test_the_explainer_cannot_reach_the_verdict():
    """Rule R5, checked in the source: the explanation layer may not construct or mutate a Verdict."""
    source = (REPO / "src" / "cfgverify" / "explain" / "explainer.py").read_text()
    assert "Verdict(" not in source
    for forbidden in ("decide(", "decide_function("):
        assert forbidden not in source, f"explainer.py contains {forbidden!r}"
    # It may READ report.verdict as often as it likes; it may never ASSIGN to one. `==` is a comparison,
    # so the pattern has to exclude it explicitly.
    assert not re.search(r"\breport\.\w+\s*=(?!=)", source), "explainer.py assigns to the evidence record"
