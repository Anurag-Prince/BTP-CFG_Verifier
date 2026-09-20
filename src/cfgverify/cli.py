"""Command-line interface: `cfgverify <command>` (or `python -m cfgverify <command>`).

Part 1 commands
    env        show and record toolchain versions (build/toolchain.json)
    emit-ir    C -> LLVM IR with the project's canonical flags
    llvm-cfg   LLVM's own CFG for each function -> DOT + SVG + networkx summary (smoke test / future oracle)

Part 2 command
    expected-cfgs   draw the expected CFGs of the regression programs and refresh the generated parts of
                    docs/CONTROL_FLOW_FUNDAMENTALS.md (--check: only report whether the doc is up to date)

Part 3 command
    parse-ir   read a .ll file (or compile a .c file first) and list every function's basic blocks,
               terminators, successors, predecessors and edges (--crosscheck: compare with LLVM's own views)

Part 4 command
    hll-cfg    C or .ll files -> one HLL CFG per function: JSON (schema cfgverify.cfg/1), Graphviz SVG,
               and an HTML gallery (--check: schema, round trips, invariants, agreement with LLVM)

Part 5 command
    asm-cfg    .s files -> one assembly CFG per function, in the same format. --isa is REQUIRED (or 'auto',
               which prints what it detected): the verified architecture is TO CONFIRM WITH PROFESSOR

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


def _cmd_expected_cfgs(args: argparse.Namespace) -> int:
    from cfgverify import expected

    programs = expected.load_all(args.regression)
    doc = Path(args.doc)
    old = doc.read_text()
    new = expected.refresh_doc(old, programs)
    if args.check:
        print("up to date" if new == old else f"{doc} is out of date: run `cfgverify expected-cfgs`")
        return 0 if new == old else 1
    svg_dir = Path(args.svg_dir)
    count = 0
    for prog in programs:
        for fn in prog.functions:
            expected.render_svg(fn, svg_dir / expected.svg_name(fn))
            count += 1
    if new != old:
        doc.write_text(new)
    print(f"{count} expected CFGs drawn in {svg_dir}/; {doc} {'updated' if new != old else 'already up to date'}")
    return 0


def _fmt_succ(b) -> str:
    return " ".join(f"{s.role}:{s.target}" for s in b.terminator.successors) or "-"


def _cmd_parse_ir(args: argparse.Namespace) -> int:
    from cfgverify.hll import ir_reader
    from cfgverify.hll.frontend import FrontendError, emit_llvm_ir

    src = Path(args.input)
    if src.suffix == ".c":
        try:
            ll = emit_llvm_ir(src, args.output, target=args.target,
                              extra_flags=("-g",) if args.debug_info else ())
        except FrontendError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        ll = src
    try:
        module = ir_reader.read_ir(ll)
    except ir_reader.IRParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    names = args.function or list(module.functions)
    missing = [n for n in names if n not in module.functions]
    if missing:
        print(f"ERROR: no such function(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    print(f"{ll}: target {module.target_triple}, {len(module.functions)} defined function(s), "
          f"{len(module.declarations)} declaration(s)")
    for name in names:
        fn = module.functions[name]
        decisions = sum(1 for b in fn.blocks if b.terminator.kind == "cond_br")
        print(f"\n@{name}  (.ll line {fn.line}): {len(fn.blocks)} blocks, {len(fn.edges())} edges, "
              f"{decisions} conditional branches, entry %{fn.entry.name}")
        rows = []
        for b in fn.blocks:
            cond = fn.condition_of(b)
            if cond:
                ctext = f"{cond.opcode} {cond.predicate or ''} {', '.join(cond.operands)}".replace("  ", " ")
            elif b.terminator.kind == "cond_br":
                ctext = f"{b.terminator.condition} (constant or not defined by a compare)"
            else:
                ctext = ""
            rows.append((f"%{b.name}", str(len(b.instructions)), b.terminator.kind, _fmt_succ(b),
                         ",".join(b.predecessors) or "-", ctext, b))
        head = ("block", "insts", "terminator", "successors (role:target)", "predecessors", "condition")
        widths = [max(len(head[k]), *(len(r[k]) for r in rows)) for k in range(5)]
        fmt = "  {:<%d}  {:>%d}  {:<%d}  {:<%d}  {:<%d}  {}" % tuple(widths)
        print(fmt.format(*head))
        for *cols, b in rows:
            print(fmt.format(*cols))
            if args.instructions:
                for inst in b.instructions:
                    src_line = f"  [C line {inst.source_line}]" if inst.source_line else ""
                    print(f"      {inst.line:>5}: {inst.text}{src_line}")
        unsupported = fn.unsupported_terminators()
        if unsupported:
            kinds = sorted({b.terminator.kind for b in unsupported})
            print(f"  note: terminator(s) outside the Phase A subset: {', '.join(kinds)} "
                  f"(read correctly; later stages report them as UNSUPPORTED)")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ir_reader.module_to_dict(module), indent=2))
        print(f"\nJSON written to {out}")

    if args.crosscheck:
        from cfgverify.hll.ir_crosscheck import crosscheck

        problems = crosscheck(ll, module)
        if problems:
            print("\nCROSS-CHECK FAILED:")
            for p in problems:
                print(f"  - {p}")
            return 2
        print("\nCross-check: blocks, edges and predecessors agree with opt dot-cfg, LLVM's preds comments and llvmlite.")
    return 0


def _cmd_hll_cfg(args: argparse.Namespace) -> int:
    from cfgverify.hll import cfg_builder, cfg_checks
    from cfgverify.hll.frontend import FrontendError
    from cfgverify.hll.ir_reader import IRParseError
    from cfgverify.model import cfg_dot

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gallery, failures, total = [], 0, 0
    print(f"{'program':<22}{'function':<22}{'blocks':>7}{'edges':>7}{'dec':>5}{'loops':>7}{'back':>6}  "
          f"{'check':<6}notes")
    for src in args.inputs:
        src = Path(src)
        ll = out / f"{src.stem}.ll" if src.suffix == ".c" else src
        try:
            cfgs = cfg_builder.cfgs_from_file(src, ll_out=ll if src.suffix == ".c" else None,
                                              target=args.target, debug_info=not args.no_debug_info)
        except (FrontendError, IRParseError) as exc:
            print(f"ERROR: {src}: {exc}", file=sys.stderr)
            return 1
        names = args.function or list(cfgs)
        results = cfg_checks.check_cfgs(cfgs, ll) if args.check else {}
        for name in names:
            if name not in cfgs:
                print(f"ERROR: {src}: no function {name!r}", file=sys.stderr)
                return 1
            cfg, total = cfgs[name], total + 1
            base = f"{src.stem}.{name}"
            (out / f"{base}.cfg.json").write_text(cfg.to_json())
            if not args.no_svg:
                cfg_dot.render(cfg, out / f"{base}.svg", detail=args.detail)
            a = cfg.analysis
            problems = results.get(name, [])
            failures += bool(problems)
            status = "-" if not args.check else ("FAIL" if problems else "ok")
            notes = f"unsupported: {', '.join(a['unsupported'])}" if a["unsupported"] else ""
            print(f"{src.name:<22}{name:<22}{a['blocks']:>7}{a['edges']:>7}{a['decisions']:>5}{len(a['loops']):>7}"
                  f"{len(a['back_edges']):>6}  {status:<6}{notes}")
            for p in problems:
                print(f"    - {p}")
            summary = (f"{a['blocks']} blocks, {a['edges']} edges, {a['decisions']} decisions, "
                       f"{len(a['loops'])} loop(s), cyclomatic {a['cyclomatic']}")
            gallery.append({"program": src.name, "function": name, "svg": f"{base}.svg",
                            "json": f"{base}.cfg.json", "summary": summary})
    if not args.no_svg:
        page = cfg_dot.write_gallery(gallery, out / "index.html")
        print(f"\n{total} CFG(s): JSON + SVG in {out}/, gallery {page}")
    else:
        print(f"\n{total} CFG(s): JSON in {out}/")
    if args.check:
        if failures:
            print(f"CHECK FAILED for {failures} function(s)")
            return 2
        print("Check: every CFG passed schema, JSON/NetworkX round trips, invariants, and agreement with LLVM dot-cfg.")
    return 0


def _cmd_asm_cfg(args: argparse.Namespace) -> int:
    from cfgverify.asm import cfg_builder, cfg_checks, isa as isa_registry
    from cfgverify.asm.reader import read_asm
    from cfgverify.model import cfg_dot

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gallery, failures, total = [], 0, 0
    print(f"{'file':<26}{'isa':<16}{'function':<20}{'blocks':>7}{'edges':>7}{'dec':>5}{'loops':>7}{'back':>6}  "
          f"{'check':<6}notes")
    for source in args.inputs:
        source = Path(source)
        try:
            asm = read_asm(source, args.isa)
        except (isa_registry.UnknownIsa, OSError) as exc:
            print(f"ERROR: {source}: {exc}", file=sys.stderr)
            return 1
        if asm.detection:
            ranked = ", ".join(f"{n}={v}" for n, v in sorted(asm.detection.items(), key=lambda kv: -kv[1]) if v)
            print(f"  detected ISA for {source.name}: {asm.isa.name} ({ranked}). "
                  f"The verified architecture is still TO CONFIRM WITH PROFESSOR.")
        cfgs = cfg_builder.build_cfgs(asm, c_source=args.c_source)
        results = cfg_checks.check_cfgs(cfgs, asm, source) if args.check else {}
        for name in (args.function or list(cfgs)):
            if name not in cfgs:
                print(f"ERROR: {source}: no function {name!r}", file=sys.stderr)
                return 1
            cfg, total = cfgs[name], total + 1
            base = f"{source.stem}.{asm.isa.name}.{name}"
            (out / f"{base}.cfg.json").write_text(cfg.to_json())
            if not args.no_svg:
                cfg_dot.render(cfg, out / f"{base}.svg", detail=args.detail)
            a = cfg.analysis
            problems = results.get(name, [])
            failures += bool(problems)
            status = "-" if not args.check else ("FAIL" if problems else "ok")
            notes = f"unsupported: {', '.join(a['unsupported'])}" if a["unsupported"] else ""
            print(f"{source.name:<26}{asm.isa.name:<16}{name:<20}{a['blocks']:>7}{a['edges']:>7}"
                  f"{a['decisions']:>5}{len(a['loops']):>7}{len(a['back_edges']):>6}  {status:<6}{notes}")
            for p in problems:
                print(f"    - {p}")
            gallery.append({"program": f"{source.name} [{asm.isa.name}]", "function": name,
                            "svg": f"{base}.svg", "json": f"{base}.cfg.json",
                            "summary": f"{a['blocks']} blocks, {a['edges']} edges, {a['decisions']} decisions, "
                                       f"{len(a['loops'])} loop(s), cyclomatic {a['cyclomatic']}"})
    if not args.no_svg:
        print(f"\n{total} CFG(s): JSON + SVG in {out}/, gallery {cfg_dot.write_gallery(gallery, out / 'index.html', 'Assembly CFGs')}")
    else:
        print(f"\n{total} CFG(s): JSON in {out}/")
    if args.check:
        if failures:
            print(f"CHECK FAILED for {failures} function(s)")
            return 2
        print("Check: schema, round trips, invariants, compiler block annotations, assembler and branch targets all agree.")
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

    x = sub.add_parser("expected-cfgs", help="draw the regression programs' expected CFGs and refresh the Part 2 doc")
    x.add_argument("--regression", default="regression", help="regression directory (default: regression)")
    x.add_argument("--svg-dir", default="docs/cfg", help="where to write the SVGs (default: docs/cfg)")
    x.add_argument("--doc", default="docs/CONTROL_FLOW_FUNDAMENTALS.md", help="study document to refresh")
    x.add_argument("--check", action="store_true", help="only check that the document is up to date")
    x.set_defaults(func=_cmd_expected_cfgs)

    r = sub.add_parser("parse-ir", help="list blocks, terminators, successors, predecessors and edges of IR")
    r.add_argument("input", help="LLVM IR file (.ll) or C file (.c, compiled first)")
    r.add_argument("-o", "--output", help="where to write the .ll when the input is C (default: build/<name>.ll)")
    r.add_argument("--target", help="target triple when compiling C (default: host)")
    r.add_argument("-g", "--debug-info", action="store_true", help="compile C with -g to get C source lines")
    r.add_argument("-f", "--function", action="append", help="only this function (repeatable)")
    r.add_argument("-i", "--instructions", action="store_true", help="also print every instruction")
    r.add_argument("--json", help="also write the full result as JSON")
    r.add_argument("--crosscheck", action="store_true", help="compare with opt dot-cfg, LLVM preds comments, llvmlite")
    r.set_defaults(func=_cmd_parse_ir)

    h = sub.add_parser("hll-cfg", help="C/.ll files -> HLL CFG per function (JSON, SVG, HTML gallery)")
    h.add_argument("inputs", nargs="+", help="C files (.c) and/or LLVM IR files (.ll)")
    h.add_argument("-o", "--out-dir", default=str(config.BUILD_DIR / "hll_cfg"), help="output directory")
    h.add_argument("-f", "--function", action="append", help="only this function (repeatable)")
    h.add_argument("--detail", choices=["ops", "ir", "none"], default="ops",
                   help="what the diagram shows inside each node (default: normalized operations)")
    h.add_argument("--no-svg", action="store_true", help="JSON only")
    h.add_argument("--no-debug-info", action="store_true", help="compile C without -g (no C line numbers)")
    h.add_argument("--target", help="target triple when compiling C (default: host)")
    h.add_argument("--check", action="store_true",
                   help="validate schema, JSON/NetworkX round trips, invariants, agreement with LLVM dot-cfg")
    h.set_defaults(func=_cmd_hll_cfg)

    a = sub.add_parser("asm-cfg", help="assembly files -> assembly CFG per function (JSON, SVG, HTML gallery)")
    a.add_argument("inputs", nargs="+", help="assembly files (.s)")
    a.add_argument("--isa", required=True,
                   help="architecture: " + ", ".join(__import__("cfgverify.asm.isa", fromlist=["x"]).available())
                        + ", or 'auto' (prints what it detected). REQUIRED: the target is TO CONFIRM [A1]")
    a.add_argument("-o", "--out-dir", default=str(config.BUILD_DIR / "asm_cfg"), help="output directory")
    a.add_argument("-f", "--function", action="append", help="only this function (repeatable)")
    a.add_argument("--c-source", help="the C file this assembly came from, to show source lines")
    a.add_argument("--detail", choices=["ops", "ir", "none"], default="ops",
                   help="what the diagram shows in each node (ops = normalized, ir = raw instructions)")
    a.add_argument("--no-svg", action="store_true", help="JSON only")
    a.add_argument("--check", action="store_true",
                   help="schema, round trips, invariants, compiler block annotations, assembler, branch targets")
    a.set_defaults(func=_cmd_asm_cfg)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
