#!/usr/bin/env bash
# Part 4 checkpoint: different C files automatically produce HLL CFGs with the SAME implementation.
# Each CFG is written as JSON (schema cfgverify.cfg/1) and SVG, and checked: schema, JSON and NetworkX round trips,
# structural invariants, and agreement with LLVM's own CFG (opt -passes=dot-cfg).
#
#   bash scripts/checkpoint_part4.sh                # all regression programs + examples
#   bash scripts/checkpoint_part4.sh my_file.c ...  # any C (or .ll) files of your own
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

if [ "$#" -gt 0 ]; then FILES=("$@"); else FILES=(regression/c/*.c examples/c/part3/*.c examples/c/hello_cfg.c); fi

step "Implementation used for every input (checksums)"
sha256sum src/cfgverify/hll/ir_reader.py src/cfgverify/hll/ir_ops.py src/cfgverify/hll/cfg_builder.py \
          src/cfgverify/model/ops.py src/cfgverify/model/cfg.py src/cfgverify/model/cfg_dot.py

step "${#FILES[@]} file(s) -> HLL CFGs (JSON + SVG + gallery in build/part4/) with checks"
$PY -m cfgverify hll-cfg "${FILES[@]}" -o build/part4 --check

step "HLL CFG tests: expected CFGs, all repo files, naming independence, unit tests, random programs (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=50})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_hll_cfg.py

printf '\nCHECKPOINT PART 4 PASSED: every input produced checked HLL CFGs with the same implementation.\n'
printf 'Open build/part4/index.html (from WSL: explorer.exe build/part4/index.html) to browse all CFGs.\n'
