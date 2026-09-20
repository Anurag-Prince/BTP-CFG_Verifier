#!/usr/bin/env bash
# Part 2 check: the regression programs, their expected CFGs, and the study document agree with LLVM.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

step "[1/3] Is docs/CONTROL_FLOW_FUNDAMENTALS.md up to date with regression/expected/*.toml?"
$PY -m cfgverify expected-cfgs --check

step "[2/3] LLVM's own CFGs of every regression program (for side-by-side viewing: build/regression/llvm_cfg/)"
for c in regression/c/*.c; do
    name=$(basename "$c" .c)
    $PY -m cfgverify emit-ir "$c" -o "build/regression/ir/$name.ll" > /dev/null
    $PY -m cfgverify llvm-cfg "build/regression/ir/$name.ll" -o "build/regression/llvm_cfg/$name" | tail -n +3
done

step "[3/3] Regression tests (expected CFG vs LLVM, conditions, loops, source lines, self-checks, other targets)"
$PY -m pytest -q tests/test_regression_cfgs.py

printf '\nPART 2 CHECK PASSED: all expected CFGs agree with LLVM.\n'
printf 'Expected diagrams: docs/cfg/*.svg   LLVM diagrams: build/regression/llvm_cfg/*/*.svg\n'
