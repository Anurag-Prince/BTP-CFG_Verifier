#!/usr/bin/env bash
# Part 8 checkpoint: the system is NOT built around the ten regression programs.
#
# The requirement is negative — "adding a new valid supported C program must not require modifying the CFG
# generator, normalizer or matcher" — so the checkpoint is built to be able to FAIL:
#
#   1. take the sha256 of every implementation file before running anything;
#   2. grep the implementation for the name of any program or function in the repo;
#   3. run the full pipeline over programs the implementation has never seen — hand-written ones added after
#      it was finished, and freshly generated ones — on every candidate architecture;
#   4. take the checksums again and require them to be identical.
#
#   bash scripts/checkpoint_part8.sh                # the unseen corpus + 40 generated programs
#   bash scripts/checkpoint_part8.sh my_program.c   # or your own files (assembly is generated for them)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

OUT=build/part8
GENERATED=${CFGVERIFY_GENERATED:-40}
rm -rf "$OUT"; mkdir -p "$OUT/inputs"

#: Every file that would have to change if the system were hard-coded for particular programs.
IMPLEMENTATION=(
    src/cfgverify/hll/ir_reader.py src/cfgverify/hll/ir_ops.py src/cfgverify/hll/cfg_builder.py
    src/cfgverify/asm/reader.py src/cfgverify/asm/cfg_builder.py
    src/cfgverify/asm/isa/base.py src/cfgverify/asm/isa/x86.py src/cfgverify/asm/isa/aarch64.py
    src/cfgverify/asm/isa/riscv.py
    src/cfgverify/normalize/semantics.py src/cfgverify/normalize/conditions.py
    src/cfgverify/normalize/loops.py src/cfgverify/normalize/rules.py
    src/cfgverify/match/invariants.py src/cfgverify/match/matcher.py src/cfgverify/match/diagnose.py
    src/cfgverify/report/verdict.py src/cfgverify/pipeline.py
)

step "The pipeline, for every input pair"
$PY main.py --steps

step "Checksums of the CFG generator, normalizer and matcher BEFORE any of this runs"
sha256sum "${IMPLEMENTATION[@]}" | tee "$OUT/checksums.before"
printf '  %s file(s)\n' "${#IMPLEMENTATION[@]}"

step "No program name, function name or file name appears in the implementation"
$PY - <<'EOF'
import re
from pathlib import Path

names = set()
for folder in ("regression/c", "examples/c/part3", "examples/c/unseen", "examples/c"):
    names |= {p.stem for p in Path(folder).glob("*.c")}
for folder in ("regression/c", "examples/c/part3", "examples/c/unseen"):
    for path in Path(folder).glob("*.c"):
        names |= set(re.findall(r"^\w[\w ]*?\b(\w+)\s*\(", path.read_text(), re.M))
# C keywords, plus three words that are function names in the corpus AND ordinary vocabulary of the
# implementation: `sign` is an x86/AArch64 condition-code name, `mix` and `classify` are English.
keywords = {"for", "while", "if", "else", "do", "switch", "return", "int", "void", "char", "main",
            "sizeof", "break", "continue", "sequence", "straight", "search", "ranges", "digits",
            "sign", "mix", "classify"}
names -= keywords

bad = []
for name in sorted(Path("src/cfgverify").rglob("*.py")):
    if name.parts[-2] in ("expected",) or name.name in ("expected.py", "cli.py", "config.py"):
        continue
    code = re.sub(r'"""(.|\n)*?"""', "", name.read_text())
    code = "\n".join(line.split("#")[0] for line in code.splitlines())
    bad += [f"{name}: {token}" for token in sorted(names) if re.search(rf"\b{re.escape(token)}\b", code)]
print(f"  checked {len(names)} program and function names against the implementation")
print("  " + ("none of them appears in it" if not bad else "FOUND: " + ", ".join(bad)))
raise SystemExit(1 if bad else 0)
EOF

step "Inputs the implementation has never seen"
if [ "$#" -gt 0 ]; then
    for f in "$@"; do cp "$f" "$OUT/inputs/"; done
else
    cp examples/c/unseen/*.c "$OUT/inputs/"
    $PY - "$GENERATED" <<'EOF'
import sys
sys.path.insert(0, "tests")
from pathlib import Path
from random_c import random_program
count = int(sys.argv[1])
for seed in range(count):
    Path(f"build/part8/inputs/generated_{seed:03d}.c").write_text(random_program(seed + 5000).source)
print(f"  {count} freshly generated program(s) + the hand-written unseen ones")
EOF
fi
printf '  %s C file(s) in %s/inputs\n' "$(ls "$OUT"/inputs/*.c | wc -l)" "$OUT"

declare -A TRIPLE=([x86-64]=x86_64-linux-gnu [aarch64]=aarch64-linux-gnu [riscv]=riscv64-linux-gnu)
for ISA in x86-64 aarch64 riscv; do
    step "Every unseen program -> ${TRIPLE[$ISA]} assembly -> the same pipeline (python main.py)"
    DIR="$OUT/$ISA"
    mkdir -p "$DIR"
    for f in "$OUT"/inputs/*.c; do
        base=$(basename "${f%.c}")
        cp "$f" "$DIR/"
        clang --target="${TRIPLE[$ISA]}" -S -O0 -g -fno-discard-value-names -o "$DIR/$base.s" "$f" 2>/dev/null
    done
    $PY main.py --batch "$DIR" --isa "$ISA" -o "$OUT/reports/$ISA" | tail -n 3
done

step "A single pair, the way the brief writes it, with an explanation"
FIRST=$(ls "$OUT"/x86-64/*.c | head -1)
$PY main.py --source "$FIRST" --assembly "${FIRST%.c}.s" --explain -o "$OUT/example" | tail -n 22

step "Checksums AFTER running every one of them"
sha256sum "${IMPLEMENTATION[@]}" > "$OUT/checksums.after"
if diff -q "$OUT/checksums.before" "$OUT/checksums.after" >/dev/null; then
    printf '  identical: not one byte of the CFG generator, normalizer or matcher changed.\n'
else
    printf '  CHANGED:\n'; diff "$OUT/checksums.before" "$OUT/checksums.after" || true
    exit 1
fi

step "Pipeline tests: general input, batch mode, the explanation guardrails, unseen programs
     (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=20})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_pipeline.py

printf '\nCHECKPOINT PART 8 PASSED: programs the implementation had never seen were verified by the same\n'
printf 'pipeline, on 3 candidate architectures, with the CFG generator, normalizer and matcher unchanged.\n'
printf 'Reports: %s/reports/   Worked example: %s/example/\n' "$OUT" "$OUT"
printf 'The ten programs in regression/c/ are REGRESSION TESTS, not the supported input set.\n'
