"""Part 7: compare a normalized HLL CFG with a normalized assembly CFG and say MATCH or MISMATCH.

    invariants.py   L1 fingerprints: the numbers any two isomorphic CFGs must agree on
    matcher.py      L0 function pairing, L2/L3 isomorphism, the partial mapping used to localize, L4 conditions
    diagnose.py     the located findings (MISSING_BLOCK, EXTRA_EDGE, WRONG_BRANCH, ...)

`verify()` here is the whole pipeline in one call: raw CFGs in, a `Verdict` out. The verdict itself is built
by `report/verdict.py` and by nothing else (rule R4).
"""

from __future__ import annotations

from cfgverify.model.cfg import CFG
from cfgverify.model.evidence import Mapping, Verdict, new_finding
from cfgverify.match import diagnose as diagnose_module
from cfgverify.match import invariants, matcher
from cfgverify.normalize import normalize
from cfgverify.report import verdict as verdict_module

__all__ = ["verify", "verify_normalized", "invariants", "matcher"]


def verify(hll_cfgs: dict[str, CFG], asm_cfgs: dict[str, CFG], *, level: str = matcher.DEFAULT_LEVEL,
           require_conditions: bool = False, allow_unsupported: bool = True,
           normalize_first: bool = True, meta: dict | None = None) -> Verdict:
    """The whole Part 7 pipeline: normalize both sides, pair the functions, match, diagnose, decide.

    `hll_cfgs` and `asm_cfgs` are {function name: CFG} as Parts 4 and 5 produce them. With
    `normalize_first=False` they are expected to be normalized already.
    """
    if normalize_first:
        hll_cfgs = {name: normalize(cfg)[0] for name, cfg in hll_cfgs.items()}
        asm_cfgs = {name: normalize(cfg)[0] for name, cfg in asm_cfgs.items()}
    return verify_normalized(hll_cfgs, asm_cfgs, level=level, require_conditions=require_conditions,
                             allow_unsupported=allow_unsupported, meta=meta)


def verify_normalized(hll_cfgs: dict[str, CFG], asm_cfgs: dict[str, CFG], *,
                      level: str = matcher.DEFAULT_LEVEL, require_conditions: bool = False,
                      allow_unsupported: bool = True, meta: dict | None = None) -> Verdict:
    counter = [0]
    paired, only_hll, only_asm = matcher.pair_functions(hll_cfgs, asm_cfgs)
    results = []

    for name in paired:
        hll, asm = hll_cfgs[name], asm_cfgs[name]
        result = matcher.match(hll, asm, level=level)
        findings = diagnose_module.diagnose(hll, asm, result, counter)
        mapping = Mapping(
            function=name,
            pairs=tuple(sorted(result.pairs.items())),
            method=result.method,
            complete=not result.hll_unmapped and not result.asm_unmapped
                     and len(result.pairs) == len(hll.nodes) == len(asm.nodes),
            hll_unmapped=tuple(result.hll_unmapped), asm_unmapped=tuple(result.asm_unmapped),
        )
        results.append(verdict_module.decide_function(
            name, findings, result, mapping,
            _summary(hll, result.hll_fingerprint), _summary(asm, result.asm_fingerprint),
            require_conditions=require_conditions, allow_unsupported=allow_unsupported))

    for name in only_hll:
        finding = new_finding(counter, "FUNCTION_MISSING", "error", name,
                              f"{name} is defined in the C program but not in the assembly [B4]")
        results.append(verdict_module.decide_function(
            name, [finding], None, None, _summary(hll_cfgs[name]), {},
            require_conditions=require_conditions, allow_unsupported=allow_unsupported))
    for name in only_asm:
        finding = new_finding(counter, "FUNCTION_EXTRA", "error", name,
                              f"{name} is defined in the assembly but not in the C program [B4]")
        results.append(verdict_module.decide_function(
            name, [finding], None, None, {}, _summary(asm_cfgs[name]),
            require_conditions=require_conditions, allow_unsupported=allow_unsupported))

    results.sort(key=lambda r: r.function)
    return verdict_module.decide(results, meta={"level": level,
                                                "require_conditions": require_conditions,
                                                "consistency_level_status": "TO CONFIRM WITH PROFESSOR [C1]",
                                                **(meta or {})},
                                 allow_unsupported=allow_unsupported)


def _summary(cfg: CFG, fingerprint: dict | None = None) -> dict:
    fingerprint = dict(fingerprint or invariants.fingerprint(cfg))
    fingerprint["origin"] = cfg.origin
    for key in ("isa", "syntax", "asm_file", "ir_file", "c_file"):
        if cfg.meta.get(key):
            fingerprint[key] = cfg.meta[key]
    return fingerprint
