"""Stage 1: C source -> LLVM IR (.ll), using Clang with the project's canonical flags.

Part 1 module. The flags come from cfgverify.config.IR_FLAGS; see docs/ARCHITECTURE.md §7.1 for why each one is used.
"""

from __future__ import annotations

import re
from pathlib import Path

from cfgverify import config
from cfgverify.toolchain import ToolchainError, require_tool, run


class FrontendError(RuntimeError):
    """Clang could not translate the C file (the message includes Clang's diagnostics)."""


def default_ir_path(c_file: Path) -> Path:
    return config.BUILD_DIR / (Path(c_file).stem + ".ll")


def emit_llvm_ir(
    c_file: str | Path,
    out: str | Path | None = None,
    target: str | None = None,
    extra_flags: tuple[str, ...] = (),
) -> Path:
    """Compile one C file to textual LLVM IR and return the path of the .ll file.

    target: an LLVM target triple (for example "riscv64-unknown-elf"). None means the host default.
            The verified ISA is TO CONFIRM WITH PROFESSOR [A1]/[A7], so no target is hard-coded.
    """
    c_file = Path(c_file)
    if not c_file.is_file():
        raise FrontendError(f"C source not found: {c_file}")
    out = Path(out) if out else default_ir_path(c_file)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        clang = require_tool("clang")
    except ToolchainError as exc:
        raise FrontendError(str(exc)) from exc

    cmd = [clang, *config.IR_FLAGS]
    if target:
        cmd.append(f"--target={target}")
    cmd += [*extra_flags, str(c_file), "-o", str(out)]
    proc = run(cmd)
    if proc.returncode != 0:
        raise FrontendError(f"clang failed ({' '.join(cmd)}):\n{proc.stderr.strip()}")
    return out


def ir_target_triple(ll_path: str | Path) -> str | None:
    """Read the `target triple = "..."` line from a .ll file."""
    m = re.search(r'^target triple = "([^"]+)"', Path(ll_path).read_text(), re.MULTILINE)
    return m.group(1) if m else None
