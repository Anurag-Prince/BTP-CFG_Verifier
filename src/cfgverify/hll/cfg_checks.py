"""Checks every generated HLL CFG must pass (used by `cfgverify hll-cfg --check`, the Part 4 checkpoint and tests).

For each CFG:
    1. the JSON follows model/cfg.schema.json;
    2. JSON round trip: CFG -> JSON -> CFG gives the same CFG;
    3. NetworkX round trip: CFG -> MultiDiGraph -> CFG gives the same CFG;
    4. structural invariants hold (CFG.check_invariants);
    5. LLVM agrees: the CFG's edges, by IR label and successor index, equal `opt -passes=dot-cfg` for that function;
    6. every IR instruction is accounted for: each gives exactly one operation, except llvm.dbg.* bookkeeping.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

from cfgverify.hll import llvm_view
from cfgverify.model.cfg import CFG, validate_json


def check_cfg(cfg: CFG, llvm_graph=None) -> list[str]:
    problems: list[str] = []
    doc = cfg.to_dict()
    try:
        validate_json(doc)
    except Exception as exc:  # jsonschema.ValidationError: report and continue
        problems.append(f"schema: {getattr(exc, 'message', exc)}")
    if CFG.from_json(cfg.to_json()) != cfg:
        problems.append("JSON round trip changed the CFG")
    if CFG.from_networkx(cfg.to_networkx()) != cfg:
        problems.append("NetworkX round trip changed the CFG")
    problems += cfg.check_invariants()
    for n in cfg.nodes:
        real = [i for i in n.instructions if "@llvm.dbg." not in i.text]
        if len(real) != len(n.operations):
            problems.append(f"{n.id}: {len(real)} instructions but {len(n.operations)} operations")
    if llvm_graph is not None:
        label = {n.id: n.label for n in cfg.nodes}
        ours = Counter((label[e.source], label[e.destination], e.index) for e in cfg.edges)
        theirs = Counter((u.lstrip("%"), v.lstrip("%"), d["succ_index"]) for u, v, d in llvm_graph.edges(data=True))
        if ours != theirs or {n.label for n in cfg.nodes} != {n.lstrip("%") for n in llvm_graph.nodes}:
            problems.append(f"LLVM dot-cfg disagrees: only ours {sorted((ours - theirs).elements())}, "
                            f"only LLVM {sorted((theirs - ours).elements())}")
    return problems


def check_cfgs(cfgs: dict[str, CFG], ll_path: str | Path) -> dict[str, list[str]]:
    """Run check_cfg on every function of one .ll file, including the LLVM comparison."""
    with tempfile.TemporaryDirectory() as tmp:
        dots = llvm_view.export_dot(ll_path, tmp)
        graphs = {name: llvm_view.load_dot(p) for name, p in dots.items()}
    results = {name: check_cfg(cfg, graphs.get(name)) for name, cfg in cfgs.items()}
    for missing in set(graphs) - set(cfgs):
        results[missing] = ["LLVM has this function but no CFG was generated"]
    return results
