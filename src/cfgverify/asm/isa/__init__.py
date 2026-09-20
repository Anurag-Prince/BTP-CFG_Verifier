"""ISA plug-in registry.

**No ISA is the default.** The verified architecture is TO CONFIRM WITH PROFESSOR ([A1], [A2]), so every entry
point demands one by name (`--isa`), or detects it and prints what it decided (`--isa auto`).
The plug-ins below are candidates we can already handle, not a decision.
"""

from __future__ import annotations

from cfgverify.asm.isa.aarch64 import AArch64
from cfgverify.asm.isa.base import Isa
from cfgverify.asm.isa.riscv import RiscV
from cfgverify.asm.isa.x86 import X86, X86Intel

REGISTRY: dict[str, type[Isa]] = {cls.name: cls for cls in (X86, X86Intel, AArch64, RiscV)}
_ALIASES = {alias: cls.name for cls in REGISTRY.values() for alias in cls.aliases}


class UnknownIsa(ValueError):
    pass


def available() -> list[str]:
    return list(REGISTRY)


def get_isa(name: str) -> Isa:
    key = name.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in REGISTRY:
        raise UnknownIsa(f"unknown ISA {name!r}; available: {', '.join(available())} (or 'auto')")
    return REGISTRY[key]()


def detect(text: str) -> tuple[Isa, dict[str, int]]:
    """Guess the ISA of an assembly file. The caller must PRINT the decision: nothing is assumed silently."""
    scores = {name: cls.score(text) for name, cls in REGISTRY.items()}
    if "dword ptr" in text or "qword ptr" in text:
        scores["x86-64-intel"] += 100
    best = max(scores, key=lambda n: scores[n])
    if scores[best] == 0:
        raise UnknownIsa("cannot tell which architecture this assembly is for; pass --isa explicitly")
    return get_isa(best), scores
