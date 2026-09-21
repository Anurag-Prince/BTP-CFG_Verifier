#!/usr/bin/env bash
# Part 6 checkpoint: raw CFGs become NORMALIZED CFGs without losing control-flow meaning.
#
# The same normalizer runs on both sides — nothing in normalize/rules.py knows whether a CFG came from LLVM IR
# or from assembly — and every run is checked against the raw CFG it came from on six properties:
#   P1 what every path computes   P2 which code can repeat   P3 decisions and their conditions
#   P4 reachability               P5 dominance               P6 provenance (nothing invented or lost)
#
#   bash scripts/checkpoint_part6.sh                # all regression + example programs, both sides
#   bash scripts/checkpoint_part6.sh my_program.c   # or your own C files
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

if [ "$#" -gt 0 ]; then FILES=("$@"); else FILES=(regression/c/*.c examples/c/part3/*.c); fi
OUT=build/part6
rm -rf "$OUT"; mkdir -p "$OUT/raw"

step "Implementation used for both sides (checksums)"
sha256sum src/cfgverify/normalize/semantics.py src/cfgverify/normalize/conditions.py \
          src/cfgverify/normalize/loops.py src/cfgverify/normalize/rules.py \
          src/cfgverify/normalize/checks.py

step "The semantic vocabulary (the brief's categories, plus the extensions needed to drop nothing)"
$PY - <<'EOF'
from cfgverify.normalize import semantics
for group, names in (("brief", semantics.BRIEF_CLASSES), ("extension [C5]", semantics.EXTENSION_CLASSES)):
    print(f"  {group:<16}{', '.join(names)}")
EOF

step "Raw CFGs: ${#FILES[@]} C file(s) -> HLL CFGs and assembly CFGs (3 candidate ISAs)"
$PY -m cfgverify hll-cfg "${FILES[@]}" -o "$OUT/raw" --no-svg >/dev/null
declare -A TRIPLE=([x86-64]=x86_64-linux-gnu [aarch64]=aarch64-linux-gnu [riscv]=riscv64-linux-gnu)
for ISA in x86-64 aarch64 riscv; do
    SRCS=()
    for f in "${FILES[@]}"; do
        base=$(basename "${f%.c}")
        clang --target="${TRIPLE[$ISA]}" -S -O0 -g -fno-discard-value-names -o "$OUT/raw/$base.$ISA.s" "$f"
        SRCS+=("$OUT/raw/$base.$ISA.s")
    done
    $PY -m cfgverify asm-cfg "${SRCS[@]}" --isa "$ISA" -o "$OUT/raw" --no-svg >/dev/null
done
printf '  %s raw CFG(s) in %s/raw\n' "$(ls "$OUT"/raw/*.cfg.json | wc -l)" "$OUT"

step "NORMALIZE every one of them with the same rules, and prove the meaning survived"
$PY -m cfgverify normalize "$OUT"/raw/*.cfg.json -o "$OUT" --check --no-svg | tail -n 8

step "A worked example, rule by rule (HLL and assembly side by side)"
$PY -m cfgverify normalize "$OUT"/raw/while.sum_to.cfg.json "$OUT"/raw/while.aarch64.aarch64.sum_to.cfg.json \
    -o "$OUT/example" --check -v

step "What normalization is FOR: do the two sides come closer? (a measurement, not a verdict)"
$PY - <<'EOF'
from pathlib import Path
from cfgverify.model.cfg import CFG
from cfgverify.normalize import normalize, loops
raw = {p.name: CFG.from_json(p.read_text()) for p in Path("build/part6/raw").glob("*.cfg.json")}
hll = {c.function: c for c in raw.values() if c.origin == "hll"}
print(f"  {'isa':<10}{'functions':>10}{'same (blocks, edges) raw':>26}{'after normalization':>22}")
for isa in ("x86-64", "aarch64", "riscv"):
    n = before = after = 0
    for name, c in raw.items():
        if c.origin != "asm" or c.meta.get("isa") != isa or c.function not in hll:
            continue
        h = hll[c.function]
        n += 1
        before += (h.analysis["blocks"], h.analysis["edges"]) == (c.analysis["blocks"], c.analysis["edges"])
        hn, _ = normalize(h)
        an, _ = normalize(c)
        after += (hn.analysis["blocks"], hn.analysis["edges"]) == (an.analysis["blocks"], an.analysis["edges"])
    print(f"  {isa:<10}{n:>10}{before:>26}{after:>22}")
print("  Matching is Part 7; this only shows that normalization removes differences instead of adding them.")
EOF

step "Normalization tests: vocabulary, conditions, loop signatures, rules, mutations, CLI, random programs
     (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=20})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_normalize.py

printf '\nCHECKPOINT PART 6 PASSED: every raw CFG, from both sides and every candidate ISA, became a normalized\n'
printf 'CFG that provably keeps its control-flow meaning (P1-P6), using one rule set for both sides.\n'
printf 'Rule logs and normalized CFGs: %s/   Worked example: %s/example/\n' "$OUT" "$OUT"
printf 'REMINDER: the rule set N1-N8 is a PROPOSAL, TO CONFIRM WITH PROFESSOR [C5].\n'
