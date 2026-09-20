"""Command-line interface: `cfgverify <command>` (or `python -m cfgverify <command>`).

Part 1 commands
    env        show and record toolchain versions (build/toolchain.json)
    emit-ir    C -> LLVM IR with the project's canonical flags
    llvm-cfg   LLVM's own CFG for each function -> DOT + SVG + networkx summary (smoke test / future oracle)

Later parts add `verify <file.c> <file.s>` (pipeline.py). It is not available yet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cfgverify import __version__, config, toolchain


def _cmd_env(args: argparse.Namespace) -> int:
    env = toolchain.collect_environment()
    out = Path(args.json) if args.json else config.BUILD_DIR / "toolchain.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(env, indent=2))

    o = env["os"]
    print(f"OS           : {o['distro'] or o['system']}  (kernel {o['kernel']}, {o['machine']}, WSL={o['wsl']})")
    print(f"Python       : {env['python']['version']}  ({env['python']['executable']})")
    print("Required tools:")
    for info in env["required_tools"].values():
        print(f"  {info['name']:<12} {info['version'] or 'MISSING':<10} {info['path'] or ''}")
    print("Optional tools:")
    for info in env["optional_tools"].values():
        print(f"  {info['name']:<12} {info['version'] or '-':<10} {info['path'] or ''}")
    print("Python packages:")
    for pkg, ver in env["python_packages"].items():
        print(f"  {pkg:<12} {ver or 'MISSING'}")
    print(f"llvmlite's bundled LLVM : {env['llvmlite_llvm_version'] or '-'}")
    print(f"clang default target    : {env['clang_default_target'] or '-'}  (host default, NOT the verified ISA)")
    print(f"llc can emit asm for    : {len(env['llc_registered_targets'])} targets")
    print(f"IR flags                : {' '.join(env['ir_flags'])}")
    for w in env["warnings"]:
        print(f"WARNING: {w}")
    for p in env["problems"]:
        print(f"PROBLEM: {p}")
    print(f"Recorded in {out}")
    if env["problems"]:
        return 1
    return 0


def _cmd_emit_ir(args: argparse.Namespace) -> int:
    from cfgverify.hll.frontend import FrontendError, emit_llvm_ir, ir_target_triple

    try:
        ll = emit_llvm_ir(args.source, args.output, target=args.target)
    except FrontendError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"LLVM IR written to {ll}")
    print(f"target triple: {ir_target_triple(ll)}")
    blocks = toolchain.llvmlite_parse_check(ll)
    print("llvmlite parsed the IR. Defined functions (basic blocks):")
    for fn, n in blocks.items():
        print(f"  {fn:<24} {n}")
    return 0


def _cmd_llvm_cfg(args: argparse.Namespace) -> int:
    from cfgverify.hll import llvm_view

    out_dir = Path(args.output) if args.output else config.BUILD_DIR / "llvm_cfg"
    dots = llvm_view.export_dot(args.ir, out_dir)
    if not dots:
        print("No defined functions found.", file=sys.stderr)
        return 1
    print(f"LLVM reference CFGs (opt -passes=dot-cfg) in {out_dir}/")
    print(f"  {'function':<24}{'blocks':>7}{'edges':>7}{'2-way':>7}{'exits':>7}{'cyclo':>7}  svg")
    for fn, dot_path in dots.items():
        s = llvm_view.summarize(llvm_view.load_dot(dot_path))
        svg = "" if args.no_svg else llvm_view.render_svg(dot_path).name
        print(
            f"  {fn:<24}{s['blocks']:>7}{s['edges']:>7}{s['two_way_branches']:>7}"
            f"{s['exit_blocks']:>7}{s['cyclomatic']:>7}  {svg}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cfgverify", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"cfgverify {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    e = sub.add_parser("env", help="show and record toolchain versions")
    e.add_argument("--json", help="where to write the JSON record (default: build/toolchain.json)")
    e.set_defaults(func=_cmd_env)

    i = sub.add_parser("emit-ir", help="C -> LLVM IR (.ll)")
    i.add_argument("source", help="C source file")
    i.add_argument("-o", "--output", help="output .ll (default: build/<name>.ll)")
    i.add_argument("--target", help="LLVM target triple (default: host; the verified ISA is TO CONFIRM)")
    i.set_defaults(func=_cmd_emit_ir)

    c = sub.add_parser("llvm-cfg", help="LLVM's own per-function CFG -> DOT/SVG + summary")
    c.add_argument("ir", help="LLVM IR file (.ll)")
    c.add_argument("-o", "--output", help="output directory (default: build/llvm_cfg)")
    c.add_argument("--no-svg", action="store_true", help="skip SVG rendering")
    c.set_defaults(func=_cmd_llvm_cfg)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
