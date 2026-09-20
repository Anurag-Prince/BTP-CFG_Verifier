#!/usr/bin/env bash
# Part 5 checkpoint: different ASSEMBLY files for the supported architecture are processed by the SAME
# CFG generator. Every CFG is written as JSON (schema cfgverify.cfg/1) and SVG, and checked against:
# the JSON schema, JSON and NetworkX round trips, the structural invariants, the compiler's own machine
# block annotations (.LBB<n>_<n> labels and "# %bb.N" comments), the assembler, and branch-target resolution.
#
# THE VERIFIED ARCHITECTURE IS STILL "TO CONFIRM WITH PROFESSOR" ([A1], [A2]).
# Nothing here defaults to x86-64: the ISA is named on every run, and three candidate plug-ins are exercised
# so that whichever architecture the professor confirms is already covered.
#
#   bash scripts/checkpoint_part5.sh                 # all regression programs, every candidate ISA
#   bash scripts/checkpoint_part5.sh my_file.c ...   # any C files of your own
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

if [ "$#" -gt 0 ]; then FILES=("$@"); else FILES=(regression/c/*.c examples/c/part3/*.c); fi
OUT=build/part5
ASM=$OUT/asm
rm -rf "$OUT"; mkdir -p "$ASM"

step "Implementation used for every input and every architecture (checksums)"
sha256sum src/cfgverify/asm/reader.py src/cfgverify/asm/cfg_builder.py src/cfgverify/asm/cfg_checks.py \
          src/cfgverify/asm/isa/base.py src/cfgverify/asm/isa/__init__.py \
          src/cfgverify/asm/isa/x86.py src/cfgverify/asm/isa/aarch64.py src/cfgverify/asm/isa/riscv.py \
          src/cfgverify/model/ops.py src/cfgverify/model/cfg.py

step "Candidate architectures (no default; --isa is required)"
$PY - <<'EOF'
from cfgverify.asm import isa
for name, cls in sorted(isa.REGISTRY.items()):
    print(f"  {name:<16}{cls.syntax:<10}{cls.assemble_target or '-'}")
print("  The architecture to verify is TO CONFIRM WITH PROFESSOR ([A1], [A2]).")
EOF

declare -A TRIPLE=([x86-64]=x86_64-linux-gnu [aarch64]=aarch64-linux-gnu [riscv]=riscv64-linux-gnu)
for ISA in x86-64 aarch64 riscv; do
    step "${#FILES[@]} C file(s) -> ${TRIPLE[$ISA]} assembly -> CFGs (same generator, --isa $ISA)"
    SRCS=()
    for f in "${FILES[@]}"; do
        base=$(basename "${f%.c}")
        clang --target="${TRIPLE[$ISA]}" -S -O0 -g -fno-discard-value-names -o "$ASM/$base.$ISA.s" "$f"
        SRCS+=("$ASM/$base.$ISA.s")
    done
    $PY -m cfgverify asm-cfg "${SRCS[@]}" --isa "$ISA" -o "$OUT" --check
done

step "A second compiler (GCC, x86-64) read by the same generator"
GCC_SRCS=()
for f in "${FILES[@]}"; do
    base=$(basename "${f%.c}")
    gcc -S -O0 -g -o "$ASM/$base.gcc.s" "$f"
    GCC_SRCS+=("$ASM/$base.gcc.s")
done
$PY -m cfgverify asm-cfg "${GCC_SRCS[@]}" --isa x86-64 -o "$OUT" --check

step "A second syntax (Intel) read by the same generator"
INTEL_SRCS=()
for f in "${FILES[@]}"; do
    base=$(basename "${f%.c}")
    clang --target=x86_64-linux-gnu -S -O0 -g -masm=intel -o "$ASM/$base.intel.s" "$f"
    INTEL_SRCS+=("$ASM/$base.intel.s")
done
$PY -m cfgverify asm-cfg "${INTEL_SRCS[@]}" --isa x86-64-intel -o "$OUT" --check

step "Hand-written assembly (no compiler directives at all), architecture detected and printed"
$PY -m cfgverify asm-cfg regression/asm/*.s --isa auto -o "$OUT" --check

step "Assembly CFG tests: decoding, normalization, detection, every C program x every candidate ISA,
     GCC output, Intel syntax, hand-written files, CLI, random programs (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=20})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_asm_cfg.py

printf '\nCHECKPOINT PART 5 PASSED: assembly files from 3 architectures, 2 compilers, 2 syntaxes and\n'
printf 'hand-written sources were all processed by the same CFG generator, and every CFG passed every check.\n'
printf 'Open %s/index.html (from WSL: explorer.exe %s/index.html) to browse all assembly CFGs.\n' "$OUT" "$OUT"
printf 'REMINDER: the architecture to verify is still TO CONFIRM WITH PROFESSOR ([A1], [A2]).\n'
