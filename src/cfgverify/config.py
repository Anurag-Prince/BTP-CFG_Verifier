"""Single source of truth for compiler flags, tool names and output locations.

Anything that changes how IR is produced lives here so it is recorded in one place and applied the same
way everywhere (the CLI, the scripts and the tests).
"""

from __future__ import annotations

import os
from pathlib import Path

# Generated files go to ./build (relative to where the command is run). Override with CFGVERIFY_BUILD_DIR.
BUILD_DIR = Path(os.environ.get("CFGVERIFY_BUILD_DIR", "build"))

# C -> LLVM IR flags (docs/ARCHITECTURE.md §7.1).
#   -O0                       keep the IR structurally close to the C source (if/for/while shapes survive)
#   -fno-discard-value-names  keep readable block names (if.then, for.cond, ...) instead of %1, %2, ...
# Deliberately NOT set:
#   -std=...                  the C standard is TO CONFIRM WITH PROFESSOR [B1]; Clang's default is used
#   --target=...              the target triple is TO CONFIRM [A1]/[A7]; the host default is used for Part 1
IR_FLAGS: tuple[str, ...] = ("-S", "-emit-llvm", "-O0", "-fno-discard-value-names")

# External tools. "required" tools must be present for the Part 1 checkpoint.
# Every name can be overridden with an environment variable such as CFGVERIFY_CLANG=/usr/bin/clang-18.
REQUIRED_TOOLS: dict[str, list[str]] = {
    # name   : command that prints the version
    "clang": ["clang", "--version"],   # C front end: C -> LLVM IR (and native builds)
    "opt": ["opt", "--version"],       # LLVM optimizer driver; used to export LLVM's own CFG (dot-cfg)
    "dot": ["dot", "-V"],              # Graphviz renderer: DOT -> SVG
}
OPTIONAL_TOOLS: dict[str, list[str]] = {
    "llc": ["llc", "--version"],       # LLVM IR -> assembly for any registered target (useful once [A1] is known)
    "llvm-config": ["llvm-config", "--version"],
    "gcc": ["gcc", "--version"],       # second C compiler; may matter if the assembly producer is GCC [A3]
    "as": ["as", "--version"],         # GNU assembler (binutils)
    "make": ["make", "--version"],
    "git": ["git", "--version"],
}

REQUIRED_PYTHON_PACKAGES: tuple[str, ...] = ("llvmlite", "networkx", "pydot", "graphviz", "pytest", "jsonschema")
