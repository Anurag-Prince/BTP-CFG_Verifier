#!/usr/bin/env bash
# Part 3 checkpoint: a new C file is compiled to LLVM IR, and its basic blocks and edges are extracted by the
# IR reader WITHOUT changing the reader. Every result is cross-checked against three views from LLVM itself.
#
#   bash scripts/checkpoint_part3.sh                 # the checkpoint inputs in examples/c/part3/
#   bash scripts/checkpoint_part3.sh my_new_file.c   # any C file(s) of your own
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

if [ "$#" -gt 0 ]; then FILES=("$@"); else FILES=(examples/c/part3/*.c); fi

step "Reader version (these checksums must not change between inputs)"
sha256sum src/cfgverify/hll/ir_reader.py src/cfgverify/hll/ir_crosscheck.py

n=0
for c in "${FILES[@]}"; do
    n=$((n + 1))
    name=$(basename "$c" .c)
    step "[$n/${#FILES[@]}] $c -> build/part3/$name.ll -> blocks and edges (+ cross-check with LLVM)"
    $PY -m cfgverify parse-ir "$c" -o "build/part3/$name.ll" --json "build/part3/$name.json" --crosscheck
done

step "Reader tests: syntax forms, all regression programs, variants, random programs (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=100})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_ir_reader.py

printf '\nCHECKPOINT PART 3 PASSED: new C files -> LLVM IR -> basic blocks/edges, with the reader unchanged.\n'
printf 'JSON results: build/part3/*.json\n'
