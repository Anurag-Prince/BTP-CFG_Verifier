"""The whole verifier as one call: a C file and its assembly in, a verdict out.

**This module is the answer to the Part 8 requirement.** The system must not be built around the ten
regression programs; it must take *any* supported C file and its assembly and run the same eight steps. So
those eight steps live here, in order, with nothing in between that knows what the program is:

    1. read the C source
    2. generate the LLVM IR (or read a .ll that was handed to us)
    3. extract the HLL basic blocks from the IR
    4. build the HLL CFG
    5. read the corresponding assembly
    6. build the assembly CFG
    7. normalize both with the same rules
    8. match, decide, and report - and optionally explain

Nothing on this path branches on a file name, a function name or a program. Adding a new supported C program
means putting a `.c` and a `.s` in front of it; it does not mean changing the CFG generator, the normalizer or
the matcher. `scripts/checkpoint_part8.sh` proves that by taking the checksums of those modules before and
after running programs they have never seen.

Every intermediate artefact is written down (`build/<run>/`): the IR, both raw CFGs, both normalized CFGs, the
normalization rule logs, the evidence JSON and the Markdown report. A reader can check any step by hand, which
is what makes the verdict reviewable rather than merely printed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from cfgverify import __version__
from cfgverify.model.cfg import CFG
from cfgverify.model.evidence import Verdict

GENERATOR = f"cfgverify {__version__} pipeline"

#: The eight steps, in the order the brief lists them. Printed by `--steps`, and used for error reporting so
#: a failure always says which step failed rather than only what the exception was.
STEPS = (
    "read the C source",
    "generate/read the LLVM IR",
    "extract the HLL basic blocks",
    "build the HLL CFG",
    "read the assembly",
    "build the assembly CFG",
    "normalize both CFGs",
    "match, decide and report",
)


class PipelineError(RuntimeError):
    """A step failed. Carries which one, so the message can say where the input stopped being usable."""

    def __init__(self, step: str, detail: str):
        super().__init__(f"step {step!r} failed: {detail}")
        self.step, self.detail = step, detail


@dataclass
class PipelineResult:
    source: Path
    assembly: tuple[Path, ...]
    isa: str
    verdict: Verdict
    artifacts: dict[str, Path] = field(default_factory=dict)
    hll: dict[str, CFG] = field(default_factory=dict)          # normalized
    asm: dict[str, CFG] = field(default_factory=dict)          # normalized
    rule_steps: dict[str, int] = field(default_factory=dict)
    detection: dict[str, int] = field(default_factory=dict)    # when --isa auto was used
    seconds: float = 0.0
    explanation: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict.verdict == "MATCH"

    def exit_code(self) -> int:
        return {"MATCH": 0, "MISMATCH": 2, "UNSUPPORTED": 3}.get(self.verdict.verdict, 2)


def run(source: str | Path, assembly, *, isa: str = "auto", out_dir: str | Path | None = None,
        level: str = "L4", require_conditions: bool = False, strict: bool = False,
        target: str | None = None, explain: bool = False, write_reports: bool = True,
        keep_svg: bool = False) -> PipelineResult:
    """Run every step for one (source, assembly) pair. Raises `PipelineError` naming the step that failed."""
    from cfgverify.asm import cfg_builder as asm_builder
    from cfgverify.asm import isa as isa_registry
    from cfgverify.hll import cfg_builder as hll_builder
    from cfgverify.match import verify
    from cfgverify.normalize import log_to_json, normalize
    from cfgverify.report import render

    started = time.time()
    source = Path(source)
    assembly = [Path(a) for a in (assembly if isinstance(assembly, (list, tuple)) else [assembly])]
    out = Path(out_dir) if out_dir else Path("build") / "verify" / source.stem
    out.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}

    # ---- steps 1-4: the C source becomes an HLL CFG per function
    if not source.is_file():
        raise PipelineError(STEPS[0], f"{source} does not exist")
    for path in assembly:
        if not path.is_file():
            raise PipelineError(STEPS[4], f"{path} does not exist")

    ll_path = out / f"{source.stem}.ll"
    try:
        hll_raw = hll_builder.cfgs_from_file(
            source, ll_out=ll_path if source.suffix == ".c" else None, target=target, debug_info=True)
    except Exception as exc:                                  # noqa: BLE001 - reported with its step
        raise PipelineError(STEPS[1] if source.suffix == ".c" else STEPS[2], str(exc)) from exc
    if not hll_raw:
        raise PipelineError(STEPS[3], f"{source} defines no functions to verify")
    if source.suffix == ".c" and ll_path.is_file():
        artifacts["ir"] = ll_path

    # ---- steps 5-6: the assembly becomes an assembly CFG per function
    asm_raw: dict[str, CFG] = {}
    detection: dict[str, int] = {}
    for path in assembly:
        try:
            cfgs = asm_builder.cfgs_from_file(path, isa, c_source=source)
        except isa_registry.UnknownIsa as exc:
            raise PipelineError(STEPS[5], str(exc)) from exc
        except Exception as exc:                              # noqa: BLE001
            raise PipelineError(STEPS[4], f"{path}: {exc}") from exc
        for name, cfg in cfgs.items():
            if name in asm_raw:
                raise PipelineError(STEPS[5], f"{name} is defined in more than one assembly file")
            asm_raw[name] = cfg
        if cfgs:
            detection = next(iter(cfgs.values())).meta.get("isa_detection", {}) or detection
    if not asm_raw:
        raise PipelineError(STEPS[5], f"no functions found in {', '.join(str(p) for p in assembly)}")
    resolved_isa = next(iter(asm_raw.values())).meta.get("isa", isa)

    # ---- step 7: normalization, the same rules on both sides
    hll, asm, rule_steps = {}, {}, {}
    try:
        for name, cfg in hll_raw.items():
            hll[name], log = normalize(cfg)
            rule_steps[f"hll:{name}"] = len(log)
            if write_reports:
                (out / f"{name}.hll.rules.json").write_text(log_to_json(log))
        for name, cfg in asm_raw.items():
            asm[name], log = normalize(cfg)
            rule_steps[f"asm:{name}"] = len(log)
            if write_reports:
                (out / f"{name}.asm.rules.json").write_text(log_to_json(log))
    except Exception as exc:                                  # noqa: BLE001
        raise PipelineError(STEPS[6], str(exc)) from exc

    if write_reports:
        for name, cfg in hll_raw.items():
            (out / f"{name}.hll.cfg.json").write_text(cfg.to_json())
            (out / f"{name}.hll.norm.cfg.json").write_text(hll[name].to_json())
        for name, cfg in asm_raw.items():
            (out / f"{name}.asm.cfg.json").write_text(cfg.to_json())
            (out / f"{name}.asm.norm.cfg.json").write_text(asm[name].to_json())
        if keep_svg:
            from cfgverify.model import cfg_dot
            for name in hll:
                cfg_dot.render(hll[name], out / f"{name}.hll.norm.svg")
            for name in asm:
                cfg_dot.render(asm[name], out / f"{name}.asm.norm.svg")

    # ---- step 8: match, decide, report
    try:
        report = verify(hll, asm, level=level, require_conditions=require_conditions,
                        allow_unsupported=not strict, normalize_first=False,
                        meta={"c_file": str(source), "assembly": [str(p) for p in assembly],
                              "isa": resolved_isa, "pipeline": GENERATOR})
    except Exception as exc:                                  # noqa: BLE001
        raise PipelineError(STEPS[7], str(exc)) from exc

    result = PipelineResult(source=source, assembly=tuple(assembly), isa=resolved_isa, verdict=report,
                            artifacts=artifacts, hll=hll, asm=asm, rule_steps=rule_steps,
                            detection=detection, seconds=time.time() - started)

    if explain:
        from cfgverify.explain import explainer
        result.explanation = explainer.explain(report, source=source)
        if write_reports:
            path = out / f"{source.stem}.explanation.md"
            path.write_text(result.explanation)
            artifacts["explanation"] = path

    if write_reports:
        paths = render.write(report, out, stem=source.stem)
        artifacts.update(paths)
        if result.explanation:
            with paths["markdown"].open("a") as handle:
                handle.write("\n---\n\n" + result.explanation)
    return result


def find_pairs(directory: str | Path, asm_suffix: str = ".s") -> list[tuple[Path, list[Path]]]:
    """Every `x.c` in a directory that has a matching `x.s`, sorted. Used by `main.py --batch`.

    Pairing is by file stem, which is a convention of the *caller*, not of the analysis: the pipeline itself
    is given two explicit paths and never guesses.
    """
    directory = Path(directory)
    pairs = []
    for c_file in sorted(directory.glob("*.c")):
        matches = sorted(directory.glob(f"{c_file.stem}*{asm_suffix}"))
        if matches:
            pairs.append((c_file, matches))
    return pairs
