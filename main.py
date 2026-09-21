#!/usr/bin/env python3
"""C/HLL <-> assembly control-flow consistency verifier — the general entry point.

    python main.py --source program.c --assembly program.s

Give it any supported C file and the assembly that is supposed to correspond to it. It runs the whole
pipeline — IR, HLL CFG, assembly CFG, normalization of both, matching — and prints MATCH or MISMATCH with
structured evidence. It is not built around any particular program: the same command works for a file it has
never seen, and adding one does not mean changing the CFG generator, the normalizer or the matcher.

    python main.py --source p.c --assembly p.s                 # the architecture is detected and printed
    python main.py --source p.c --assembly p.s --isa aarch64   # or named explicitly
    python main.py --source p.c --assembly p.s --explain       # add a plain-English explanation
    python main.py --batch dir/                                # every x.c with a matching x.s
    python main.py --source p.c --assembly p.s --json          # machine-readable, for a script

Exit codes:  0 MATCH   2 MISMATCH   3 UNSUPPORTED   1 the input could not be processed

The architecture to verify is still TO CONFIRM WITH PROFESSOR ([A1], [A2]); `--isa` accepts any of the
candidate plug-ins, or `auto`, which detects one and says so.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cfgverify import __version__                                     # noqa: E402
from cfgverify.pipeline import STEPS, PipelineError, find_pairs, run  # noqa: E402
from cfgverify.report import render                                   # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exit codes: 0 MATCH, 2 MISMATCH, 3 UNSUPPORTED, 1 the input could not be processed.")
    p.add_argument("--version", action="version", version=f"cfgverify {__version__}")

    p.add_argument("--source", help="the C file (or .ll) to verify")
    p.add_argument("--assembly", nargs="+", help="the assembly file(s) that should correspond to it")
    p.add_argument("--batch", help="a directory: verify every x.c that has a matching x.s")

    p.add_argument("--isa", default="auto",
                   help="architecture of the assembly, or 'auto' to detect and print it (default: auto). "
                        "The architecture to verify is TO CONFIRM WITH PROFESSOR [A1]")
    p.add_argument("--target", help="target triple to compile the C with (default: the host) [A7]")
    p.add_argument("-o", "--out-dir", help="where to write the artefacts (default: build/verify/<name>)")

    p.add_argument("--level", default="L4", choices=["L1", "L2", "L3", "L4"],
                   help="how far to take the matcher (default: L4, which also compares branch conditions)")
    p.add_argument("--require-conditions", action="store_true",
                   help="a branch-condition disagreement is an error, not a warning (the strict reading of [C1])")
    p.add_argument("--strict", action="store_true",
                   help="never answer UNSUPPORTED; an unsupported construct becomes MISMATCH [C3]")

    p.add_argument("--explain", action="store_true",
                   help="add a plain-English explanation of the evidence (deterministic by default; an AI "
                        "provider is optional and its output is post-checked) [E1]")
    p.add_argument("--svg", action="store_true", help="also draw the normalized CFGs")
    p.add_argument("--json", action="store_true", help="print the evidence record instead of a table")
    p.add_argument("-q", "--quiet", action="store_true", help="the verdict line only")
    p.add_argument("--steps", action="store_true", help="print the pipeline steps and exit")
    return p


def _one(args, source: Path, assembly: list[Path], out_dir: Path | None) -> int:
    try:
        result = run(source, assembly, isa=args.isa, out_dir=out_dir, level=args.level,
                     require_conditions=args.require_conditions, strict=args.strict,
                     target=args.target, explain=args.explain, keep_svg=args.svg)
    except PipelineError as exc:
        print(f"ERROR: {source.name}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(result.verdict.to_json())
        return result.exit_code()

    if args.quiet:
        print(f"{result.verdict.verdict:<12}{source.name}  ({result.isa}, {result.seconds:.1f}s)")
    else:
        if args.isa == "auto":
            print(f"Architecture detected: {result.isa}. "
                  f"The architecture to verify is still TO CONFIRM WITH PROFESSOR.")
        print(render.terminal(result.verdict, show_findings=True))
        if result.explanation:
            print()
            print(result.explanation)
        if result.artifacts.get("markdown"):
            print(f"\nReport: {result.artifacts['markdown']}   "
                  f"Evidence: {result.artifacts['json']}")
    return result.exit_code()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.steps:
        print("The pipeline, for every input pair:")
        for number, step in enumerate(STEPS, 1):
            print(f"  {number}. {step}")
        print("\nNone of these steps branches on which program it is looking at.")
        return 0

    if args.batch:
        pairs = find_pairs(args.batch)
        if not pairs:
            print(f"ERROR: no x.c with a matching x.s in {args.batch}", file=sys.stderr)
            return 1
        print(f"{len(pairs)} program(s) in {args.batch}\n")
        codes = []
        for source, assembly in pairs:
            out = Path(args.out_dir) / source.stem if args.out_dir else None
            codes.append(_one(argparse.Namespace(**{**vars(args), "quiet": True}), source, assembly, out))
        matched = codes.count(0)
        print(f"\n{matched}/{len(codes)} MATCH, {codes.count(2)} MISMATCH, "
              f"{codes.count(3)} UNSUPPORTED, {codes.count(1)} could not be processed")
        return 0 if matched == len(codes) else max(codes)

    if not args.source or not args.assembly:
        build_parser().error("--source and --assembly are both required (or use --batch)")
    return _one(args, Path(args.source), [Path(a) for a in args.assembly],
                Path(args.out_dir) if args.out_dir else None)


if __name__ == "__main__":
    raise SystemExit(main())
