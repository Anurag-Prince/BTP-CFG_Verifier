"""Find external tools, record their versions, and check that the toolchain pieces work together.

Part 1 module. This records exactly which toolchain produced a result, because LLVM IR text changes
between LLVM versions. The result is written to build/toolchain.json by `cfgverify env`.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cfgverify import config

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
# Ubuntu can install versioned binaries only (clang-18, opt-18, ...); try those if the plain name is missing.
_VERSION_SUFFIXES = [f"-{v}" for v in range(24, 13, -1)]


class ToolchainError(RuntimeError):
    """A required external tool is missing or failed."""


def find_tool(name: str) -> str | None:
    """Return the full path of a tool, or None if it is missing.

    Order: $CFGVERIFY_<NAME> override, the plain name on PATH, then versioned names (clang-24 .. clang-14).
    """
    override = os.environ.get("CFGVERIFY_" + name.upper().replace("-", "_"))
    if override:
        return shutil.which(override) or (override if Path(override).is_file() else None)
    found = shutil.which(name)
    if found:
        return found
    for suffix in _VERSION_SUFFIXES:
        found = shutil.which(name + suffix)
        if found:
            return found
    return None


def require_tool(name: str) -> str:
    path = find_tool(name)
    if path is None:
        raise ToolchainError(
            f"'{name}' was not found on PATH. Run scripts/setup_wsl.sh, or set CFGVERIFY_{name.upper()}."
        )
    return path


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command and capture its text output. The caller checks returncode."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def tool_version(name: str, version_cmd: list[str]) -> dict:
    """Return {'name', 'path', 'version', 'banner'}. path is None if the tool is missing."""
    path = find_tool(name)
    info = {"name": name, "path": path, "version": None, "banner": None}
    if path is None:
        return info
    proc = run([path, *version_cmd[1:]])
    text = (proc.stdout or "") + (proc.stderr or "")  # some tools (dot -V) print to stderr
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    banner = next((ln for ln in lines if _VERSION_RE.search(ln)), lines[0] if lines else "")
    match = _VERSION_RE.search(banner)
    info["banner"] = banner
    info["version"] = match.group(1) if match else None
    return info


def clang_default_target() -> str | None:
    clang = find_tool("clang")
    if clang is None:
        return None
    proc = run([clang, "-print-target-triple"])
    return proc.stdout.strip() or None


def llc_registered_targets() -> list[str]:
    """Targets this LLVM build can emit assembly for (useful when [A1] is answered)."""
    llc = find_tool("llc")
    if llc is None:
        return []
    proc = run([llc, "--version"])
    targets, in_list = [], False
    for line in proc.stdout.splitlines():
        if "Registered Targets" in line:
            in_list = True
            continue
        if in_list and line.strip():
            targets.append(line.split("-")[0].strip())
    return targets


def python_packages() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for pkg in config.REQUIRED_PYTHON_PACKAGES:
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            versions[pkg] = None
    return versions


def llvmlite_llvm_version() -> str | None:
    """The LLVM version bundled inside llvmlite (it can differ from the system LLVM)."""
    try:
        llvm = importlib.import_module("llvmlite.binding")
    except ImportError:
        return None
    return ".".join(str(p) for p in llvm.llvm_version_info)


def os_info() -> dict:
    info = {
        "system": platform.system(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "distro": None,
        "wsl": False,
    }
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                info["distro"] = line.split("=", 1)[1].strip('"')
    proc_version = Path("/proc/version")
    if proc_version.exists():
        info["wsl"] = "microsoft" in proc_version.read_text().lower()
    return info


def collect_environment() -> dict:
    """Snapshot of everything that can influence results. Written to build/toolchain.json."""
    tools = {n: tool_version(n, c) for n, c in config.REQUIRED_TOOLS.items()}
    optional = {n: tool_version(n, c) for n, c in config.OPTIONAL_TOOLS.items()}
    env = {
        "os": os_info(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "required_tools": tools,
        "optional_tools": optional,
        "python_packages": python_packages(),
        "llvmlite_llvm_version": llvmlite_llvm_version(),
        "clang_default_target": clang_default_target(),
        "llc_registered_targets": llc_registered_targets(),
        "ir_flags": list(config.IR_FLAGS),
    }
    env["problems"], env["warnings"] = _diagnose(env)
    return env


def _major(version: str | None) -> int | None:
    try:
        return int(version.split(".")[0]) if version else None
    except ValueError:
        return None


def _diagnose(env: dict) -> tuple[list[str], list[str]]:
    problems, warnings = [], []
    for name, info in env["required_tools"].items():
        if info["path"] is None:
            problems.append(f"required tool missing: {name}")
    for pkg, ver in env["python_packages"].items():
        if ver is None:
            problems.append(f"required Python package missing: {pkg}")
    if sys.version_info < (3, 11):
        problems.append(f"Python >= 3.11 required, found {platform.python_version()}")

    clang_major = _major(env["required_tools"]["clang"]["version"])
    opt_major = _major(env["required_tools"]["opt"]["version"])
    lite_major = _major(env["llvmlite_llvm_version"])
    if clang_major and opt_major and clang_major != opt_major:
        warnings.append(f"clang ({clang_major}) and opt ({opt_major}) come from different LLVM releases")
    if clang_major and lite_major and clang_major > lite_major:
        warnings.append(
            f"clang {clang_major} is newer than llvmlite's LLVM {lite_major}; llvmlite may fail to read "
            "the IR. Use an older clang or a newer llvmlite."
        )
    if Path.cwd().as_posix().startswith("/mnt/") and env["os"]["wsl"]:
        warnings.append(
            "the repo is on the Windows filesystem (/mnt/...). This works, but builds are slower than "
            "on the WSL filesystem (~/)."
        )
    return problems, warnings


def llvmlite_parse_check(ll_path: Path) -> dict[str, int]:
    """Toolchain compatibility check: can llvmlite read the IR that this clang produced?

    Returns {function_name: number_of_basic_blocks} for defined functions. This is an environment check,
    not the project's IR reader (that is hll/ir_reader.py, Part 2).
    """
    import llvmlite.binding as llvm

    module = llvm.parse_assembly(Path(ll_path).read_text())
    module.verify()
    return {f.name: len(list(f.blocks)) for f in module.functions if not f.is_declaration}
