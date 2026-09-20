#!/usr/bin/env bash
# Part 1 checkpoint: C -> LLVM IR works on this machine (plus the graph/visualization stack).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
CLANG="$($PY -c 'from cfgverify.toolchain import require_tool; print(require_tool("clang"))')"
EXAMPLE=examples/c/hello_cfg.c
mkdir -p build

step() { printf '\n==> %s\n' "$*"; }

step "[1/6] Toolchain versions (recorded in build/toolchain.json)"
$PY -m cfgverify env

step "[2/6] Native build and run of $EXAMPLE (checks compiler, headers, libc and linker)"
"$CLANG" -O0 -Wall -Wextra -Werror "$EXAMPLE" -o build/hello_cfg
./build/hello_cfg

step "[3/6] C -> LLVM IR"
$PY -m cfgverify emit-ir "$EXAMPLE" -o build/hello_cfg.ll

step "[4/6] The IR of one function (classify)"
sed -n '/^define.*@classify(/,/^}/p' build/hello_cfg.ll

step "[5/6] LLVM's reference CFGs -> networkx summary + SVG (build/llvm_cfg/)"
$PY -m cfgverify llvm-cfg build/hello_cfg.ll -o build/llvm_cfg

step "[6/6] Test suite"
$PY -m pytest -q

printf '\nCHECKPOINT PART 1 PASSED: C -> LLVM IR works on this machine.\n'
printf 'Open build/llvm_cfg/classify.svg to see a CFG.\n'
