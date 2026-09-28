#!/usr/bin/env python3

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]

REGRESSION = ROOT / "regression" / "c"
EXAMPLES = ROOT / "examples" / "c" / "part3"

OUT = ROOT / "build" / "experiment"
ASM_DIR = OUT / "assembly"

OUT.mkdir(parents=True, exist_ok=True)
ASM_DIR.mkdir(parents=True, exist_ok=True)

ISAS = {
    "x86-64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "riscv": "riscv64-unknown-elf",
}


# ---------------------------------------------------------------------
# Import project implementation
# ---------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "src"))

from cfgverify.asm import cfg_builder as asm_builder
from cfgverify.hll import cfg_builder as hll_builder
from cfgverify.normalize import normalize


# ---------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------

def get_programs():
    programs = []

    for directory in (REGRESSION, EXAMPLES):
        if directory.exists():
            programs.extend(sorted(directory.glob("*.c")))

    return sorted(programs)


# ---------------------------------------------------------------------
# Assembly generation
# ---------------------------------------------------------------------

def compile_assembly(source: Path, isa: str) -> Path:
    target = ISAS[isa]

    out_dir = ASM_DIR / isa
    out_dir.mkdir(parents=True, exist_ok=True)

    output = out_dir / f"{source.stem}.s"

    cmd = [
        "clang",
        f"--target={target}",
        "-S",
        "-O0",
        "-g",
        "-fno-discard-value-names",
        str(source),
        "-o",
        str(output),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Assembly generation failed for {source} [{isa}]\n"
            f"{result.stderr}"
        )

    return output


# ---------------------------------------------------------------------
# CFG statistics
# ---------------------------------------------------------------------

def cfg_stats(cfg):
    # compute_analysis() is already called by the builders,
    # but call it again so the experiment is self-contained.
    cfg.compute_analysis()

    analysis = cfg.analysis

    return {
        "blocks": analysis.get("blocks", len(cfg.nodes)),
        "edges": analysis.get("edges", len(cfg.edges)),
        "decisions": analysis.get("decisions", 0),
        "cyclomatic": analysis.get("cyclomatic", 0),
        "back_edges": len(analysis.get("back_edges", [])),
        "loops": len(analysis.get("loops", [])),
        "unreachable": len(analysis.get("unreachable", [])),
        "unsupported": len(analysis.get("unsupported", [])),
    }


# ---------------------------------------------------------------------
# Simple structural signature
# ---------------------------------------------------------------------

def structural_agreement(hll, asm):
    """
    Compare two normalized CFGs using graph isomorphism.

    Node IDs and node ordering are ignored.
    The comparison uses structural CFG properties.
    """

    import networkx as nx

    def make_graph(cfg):
        cfg.compute_analysis()

        graph = nx.DiGraph()

        # Add nodes with structural attributes.
        for node in cfg.nodes:
            graph.add_node(
                node.id,
                is_entry=bool(node.is_entry),
                is_exit=bool(node.is_exit),
                terminator=node.terminator,
                loop_depth=node.loop_depth,
            )

        # Add edges with control-flow attributes.
        for edge in cfg.edges:
            graph.add_edge(
                edge.source,
                edge.destination,
                kind=edge.kind,
                index=edge.index,
                back_edge=bool(edge.back_edge),
            )

        return graph

    g_hll = make_graph(hll)
    g_asm = make_graph(asm)

    node_match = nx.algorithms.isomorphism.categorical_node_match(
        [
            "is_entry",
            "is_exit",
            "terminator",
            "loop_depth",
        ],
        [
            False,
            False,
            None,
            0,
        ],
    )

    edge_match = nx.algorithms.isomorphism.categorical_edge_match(
        [
            "kind",
            "index",
            "back_edge",
        ],
        [
            None,
            0,
            False,
        ],
    )

    matcher = nx.algorithms.isomorphism.DiGraphMatcher(
        g_hll,
        g_asm,
        node_match=node_match,
        edge_match=edge_match,
    )

    return matcher.is_isomorphic()

def structural_agreement(hll, asm):
    """
    Compare CFG topology using directed graph isomorphism.

    Node IDs, node ordering, terminator names, edge labels,
    and branch numbering are ignored.

    Entry and exit nodes are preserved.
    """

    import networkx as nx

    def make_graph(cfg):
        graph = nx.DiGraph()

        for node in cfg.nodes:
            graph.add_node(
                node.id,
                is_entry=bool(node.is_entry),
                is_exit=bool(node.is_exit),
            )

        for edge in cfg.edges:
            graph.add_edge(
                edge.source,
                edge.destination,
            )

        return graph

    g_hll = make_graph(hll)
    g_asm = make_graph(asm)

    node_match = nx.algorithms.isomorphism.categorical_node_match(
        ["is_entry", "is_exit"],
        [False, False],
    )

    matcher = nx.algorithms.isomorphism.DiGraphMatcher(
        g_hll,
        g_asm,
        node_match=node_match,
    )

    return matcher.is_isomorphic()
def run_case(source: Path, isa: str):

    print(f"\n[{isa}] {source.name}")

    assembly = compile_assembly(source, isa)

    target = ISAS[isa]

    # -------------------------------------------------------------
    # RAW HLL CFG
    # -------------------------------------------------------------

    hll_raw = hll_builder.cfgs_from_file(
        source,
        target=target,
        debug_info=True,
    )

    # -------------------------------------------------------------
    # RAW ASM CFG
    # -------------------------------------------------------------

    asm_raw = asm_builder.cfgs_from_file(
        assembly,
        isa,
        c_source=source,
    )

    rows = []

    # Only compare functions present on both sides.
    common_functions = sorted(set(hll_raw) & set(asm_raw))

    if not common_functions:
        raise RuntimeError(
            f"No common functions found for {source.name} [{isa}]"
        )

    for function in common_functions:

        hll_cfg_raw = hll_raw[function]
        asm_cfg_raw = asm_raw[function]

        # ---------------------------------------------------------
        # RAW
        # ---------------------------------------------------------

        hll_raw_stats = cfg_stats(hll_cfg_raw)
        asm_raw_stats = cfg_stats(asm_cfg_raw)

        raw_agree = structural_agreement(
            hll_cfg_raw,
            asm_cfg_raw,
        )

        # ---------------------------------------------------------
        # NORMALIZATION
        # ---------------------------------------------------------

        hll_norm, hll_log = normalize(hll_cfg_raw)

        asm_norm, asm_log = normalize(asm_cfg_raw)

        # ---------------------------------------------------------
        # NORMALIZED
        # ---------------------------------------------------------

        hll_norm_stats = cfg_stats(hll_norm)
        asm_norm_stats = cfg_stats(asm_norm)

        normalized_agree = structural_agreement(
            hll_norm,
            asm_norm,
        )

        # ---------------------------------------------------------
        # Rule counts
        # ---------------------------------------------------------

        rule_counts = {}

        for step in list(hll_log) + list(asm_log):
            rule_counts[step.rule] = rule_counts.get(step.rule, 0) + 1

        # ---------------------------------------------------------
        # Store result
        # ---------------------------------------------------------

        row = {
            "program": source.stem,
            "source": str(source.relative_to(ROOT)),
            "function": function,
            "isa": isa,

            "raw_hll_blocks": hll_raw_stats["blocks"],
            "raw_asm_blocks": asm_raw_stats["blocks"],

            "raw_hll_edges": hll_raw_stats["edges"],
            "raw_asm_edges": asm_raw_stats["edges"],

            "raw_hll_decisions": hll_raw_stats["decisions"],
            "raw_asm_decisions": asm_raw_stats["decisions"],

            "raw_hll_cyclomatic": hll_raw_stats["cyclomatic"],
            "raw_asm_cyclomatic": asm_raw_stats["cyclomatic"],

            "raw_hll_back_edges": hll_raw_stats["back_edges"],
            "raw_asm_back_edges": asm_raw_stats["back_edges"],

            "raw_hll_loops": hll_raw_stats["loops"],
            "raw_asm_loops": asm_raw_stats["loops"],

            "raw_hll_unreachable": hll_raw_stats["unreachable"],
            "raw_asm_unreachable": asm_raw_stats["unreachable"],

            "raw_hll_unsupported": hll_raw_stats["unsupported"],
            "raw_asm_unsupported": asm_raw_stats["unsupported"],

            "raw_agree": int(raw_agree),

            "norm_hll_blocks": hll_norm_stats["blocks"],
            "norm_asm_blocks": asm_norm_stats["blocks"],

            "norm_hll_edges": hll_norm_stats["edges"],
            "norm_asm_edges": asm_norm_stats["edges"],

            "norm_hll_decisions": hll_norm_stats["decisions"],
            "norm_asm_decisions": asm_norm_stats["decisions"],

            "norm_hll_cyclomatic": hll_norm_stats["cyclomatic"],
            "norm_asm_cyclomatic": asm_norm_stats["cyclomatic"],

            "norm_hll_back_edges": hll_norm_stats["back_edges"],
            "norm_asm_back_edges": asm_norm_stats["back_edges"],

            "norm_hll_loops": hll_norm_stats["loops"],
            "norm_asm_loops": asm_norm_stats["loops"],

            "norm_hll_unreachable": hll_norm_stats["unreachable"],
            "norm_asm_unreachable": asm_norm_stats["unreachable"],

            "norm_hll_unsupported": hll_norm_stats["unsupported"],
            "norm_asm_unsupported": asm_norm_stats["unsupported"],

            "normalized_agree": int(normalized_agree),

            "normalization_steps_hll": len(hll_log),
            "normalization_steps_asm": len(asm_log),

            "N1": rule_counts.get("N1", 0),
            "N2": rule_counts.get("N2", 0),
            "N3": rule_counts.get("N3", 0),
            "N4": rule_counts.get("N4", 0),
            "N5": rule_counts.get("N5", 0),
            "N6": rule_counts.get("N6", 0),
            "N7": rule_counts.get("N7", 0),
            "N8": rule_counts.get("N8", 0),
        }

        rows.append(row)

        print(
            f"  {function}: "
            f"raw={'AGREE' if raw_agree else 'DIFF'}, "
            f"normalized={'AGREE' if normalized_agree else 'DIFF'}"
        )

    return rows


# ---------------------------------------------------------------------
# Main experiment
# ---------------------------------------------------------------------

def main():

    programs = get_programs()

    print("=" * 70)
    print("CFG NORMALIZATION EXPERIMENT")
    print("=" * 70)

    print(f"Programs: {len(programs)}")
    print(f"ISAs:     {len(ISAS)}")
    print(f"Cases:    {len(programs) * len(ISAS)}")

    if not programs:
        raise RuntimeError("No C programs found.")

    all_rows = []

    failures = []

    for source in programs:

        for isa in ISAS:

            try:
                rows = run_case(source, isa)
                all_rows.extend(rows)

            except Exception as exc:
                print(
                    f"\nERROR: {source.name} [{isa}]\n"
                    f"  {exc}"
                )

                failures.append({
                    "program": source.stem,
                    "isa": isa,
                    "error": str(exc),
                })

    # -------------------------------------------------------------
    # Write detailed CSV
    # -------------------------------------------------------------

    if all_rows:

        csv_path = OUT / "cfg_results.csv"

        fieldnames = list(all_rows[0].keys())

        with csv_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(all_rows)

        print(f"\nDetailed results:")
        print(csv_path)

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------

    summary_rows = []

    for isa in ISAS:

        rows = [
            r for r in all_rows
            if r["isa"] == isa
        ]

        if not rows:
            continue

        raw_agree = sum(r["raw_agree"] for r in rows)
        norm_agree = sum(r["normalized_agree"] for r in rows)

        raw_rate = raw_agree / len(rows)
        norm_rate = norm_agree / len(rows)

        summary_rows.append({
            "isa": isa,
            "functions": len(rows),

            "raw_agree": raw_agree,
            "raw_total": len(rows),
            "raw_agreement_rate": raw_rate,

            "normalized_agree": norm_agree,
            "normalized_total": len(rows),
            "normalized_agreement_rate": norm_rate,

            "improvement_percentage_points":
                (norm_rate - raw_rate) * 100,

            "N1": sum(r["N1"] for r in rows),
            "N2": sum(r["N2"] for r in rows),
            "N3": sum(r["N3"] for r in rows),
            "N4": sum(r["N4"] for r in rows),
            "N5": sum(r["N5"] for r in rows),
            "N6": sum(r["N6"] for r in rows),
            "N7": sum(r["N7"] for r in rows),
            "N8": sum(r["N8"] for r in rows),
        })

    if summary_rows:

        summary_path = OUT / "summary.csv"

        fieldnames = list(summary_rows[0].keys())

        with summary_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(summary_rows)

        print(f"Summary:")
        print(summary_path)

    # -------------------------------------------------------------
    # Failure log
    # -------------------------------------------------------------

    if failures:

        failure_path = OUT / "failures.csv"

        with failure_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=["program", "isa", "error"],
            )

            writer.writeheader()
            writer.writerows(failures)

        print(f"\nFailures:")
        print(failure_path)

    # -------------------------------------------------------------
    # Final summary
    # -------------------------------------------------------------

    print("\n" + "=" * 70)
    print("EXPERIMENT COMPLETE")
    print("=" * 70)

    print(f"Successful function cases: {len(all_rows)}")
    print(f"Failed cases:              {len(failures)}")

    for row in summary_rows:

        print(
            f"\n{row['isa']}"
            f"\n  functions:       {row['functions']}"
            f"\n  raw agreement:   "
            f"{row['raw_agree']}/{row['raw_total']} "
            f"({row['raw_agreement_rate'] * 100:.2f}%)"
            f"\n  normalized:       "
            f"{row['normalized_agree']}/{row['normalized_total']} "
            f"({row['normalized_agreement_rate'] * 100:.2f}%)"
            f"\n  improvement:     "
            f"{row['improvement_percentage_points']:.2f} percentage points"
        )


if __name__ == "__main__":
    main()
