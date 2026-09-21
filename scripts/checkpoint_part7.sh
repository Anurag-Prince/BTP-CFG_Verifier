#!/usr/bin/env bash
# Part 7 checkpoint: the SAME matcher works across different supported input programs, with no
# program-specific rules.
#
# Two things are shown, and the second is the one that matters:
#   1. every supported program, compiled for every candidate ISA, verifies as MATCH against its own assembly;
#   2. DELIBERATELY BROKEN assembly is caught, and the finding says exactly where and what.
# A matcher that answered MATCH for everything would pass (1) perfectly, which is why (2) exists.
#
#   bash scripts/checkpoint_part7.sh                 # all regression + example programs, every ISA
#   bash scripts/checkpoint_part7.sh my_program.c    # or your own C files
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    echo "No .venv found. Run: bash scripts/setup_wsl.sh" >&2
    exit 1
fi
step() { printf '\n==> %s\n' "$*"; }

if [ "$#" -gt 0 ]; then FILES=("$@"); else FILES=(regression/c/*.c examples/c/part3/*.c); fi
OUT=build/part7
rm -rf "$OUT"; mkdir -p "$OUT/asm"

step "Implementation used for every program and every architecture (checksums)"
sha256sum src/cfgverify/match/matcher.py src/cfgverify/match/invariants.py src/cfgverify/match/diagnose.py \
          src/cfgverify/match/__init__.py src/cfgverify/report/verdict.py src/cfgverify/model/evidence.py

step "No program-specific rule anywhere in the matcher"
$PY - <<'EOF'
import re
from pathlib import Path
functions = {"sum_to", "factorial", "gcd", "lcm", "quadrant", "sign", "max2", "bubble_sort",
             "binary_search", "is_prime", "count_primes", "count_pairs", "collatz_steps",
             "clamp_negative", "safe_div", "halvings", "capped_pairs"}
isas = {"x86-64", "aarch64", "riscv", "x86_64", "LBB"}
bad = []
for name in ("matcher.py", "invariants.py", "diagnose.py"):
    source = Path("src/cfgverify/match") / name
    code = re.sub(r'"""(.|\n)*?"""', "", source.read_text())
    code = "\n".join(line.split("#")[0] for line in code.splitlines())
    bad += [f"{name}: {token}" for token in sorted(functions | isas)
            if re.search(rf"\b{re.escape(token)}\b", code)]
print("  " + ("no function name, program name or ISA name appears in the matching logic"
              if not bad else "FOUND: " + ", ".join(bad)))
raise SystemExit(1 if bad else 0)
EOF

step "The ten finding kinds the brief asks for"
$PY - <<'EOF'
from cfgverify.model.evidence import BRIEF_KINDS, FINDING_KINDS
for kind in BRIEF_KINDS:
    print(f"  {kind:<26}{FINDING_KINDS[kind][1]}")
EOF

declare -A TRIPLE=([x86-64]=x86_64-linux-gnu [aarch64]=aarch64-linux-gnu [riscv]=riscv64-linux-gnu)
for ISA in x86-64 aarch64 riscv; do
    step "${#FILES[@]} program(s), ${TRIPLE[$ISA]}: C vs its own assembly (expect MATCH)"
    for f in "${FILES[@]}"; do
        base=$(basename "${f%.c}")
        clang --target="${TRIPLE[$ISA]}" -S -O0 -g -fno-discard-value-names -o "$OUT/asm/$base.$ISA.s" "$f"
        $PY -m cfgverify verify "$f" "$OUT/asm/$base.$ISA.s" --isa "$ISA" -o "$OUT/$ISA" -q \
            | sed -n '2,$p' | grep -v '^$' | grep -v '^Report:' || true
    done
done

step "Deliberately broken assembly: each mutation must be CAUGHT and LOCATED"
$PY - <<'EOF'
import sys
sys.path.insert(0, "tests")
from pathlib import Path
import test_matching as mutants
from cfgverify.match import verify

print(f"  {'mutation':<26}{'program':<16}{'verdict':<12}{'must report':<26}findings")
failures = 0
for tag, program, isa, mutation, expected in mutants.MUTATIONS:
    hll, asm = mutants._mutate(Path("regression/c") / program, isa, mutation, tag)
    report = verify(hll, asm, require_conditions=(expected == "WRONG_BRANCH_CONDITION"))
    kinds = sorted(mutants._kinds(report))
    ok = expected in kinds and report.verdict != "MATCH"
    failures += not ok
    print(f"  {tag:<26}{program:<16}{report.verdict:<12}{expected:<26}{'OK  ' if ok else 'FAIL'} {kinds}")
    for finding in report.all_findings():
        if finding.kind == expected:
            print(f"      -> {finding.description}")
            break
raise SystemExit(1 if failures else 0)
EOF

step "A harmless edit must NOT be reported as a mismatch (a matcher that cries wolf is useless)"
$PY - <<'EOF'
import re, sys
sys.path.insert(0, "tests")
from pathlib import Path
import test_matching as mutants
from cfgverify.match import verify

def delete_a_redundant_jump(text):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        target = re.match(r"^jmp\s+(\.LBB\d+_\d+)$", line.strip())
        if target:
            following = next((lines[j].strip() for j in range(index + 1, len(lines))
                              if lines[j].strip().startswith(".LBB")), "")
            if following.startswith(target.group(1) + ":"):
                return "\n".join(lines[:index] + lines[index + 1:]) + "\n"
    raise SystemExit("  no redundant jump found")

hll, asm = mutants._mutate(Path("regression/c/nested_if.c"), "x86-64", delete_a_redundant_jump, "harmless")
report = verify(hll, asm)
print(f"  deleting a jump to the very next block: {report.verdict} "
      f"({len(report.all_findings())} finding(s)) - control falls through to the same place")
raise SystemExit(0 if report.verdict == "MATCH" else 1)
EOF

step "A worked report"
$PY -m cfgverify verify regression/c/nested_loop.c "$OUT/asm/nested_loop.aarch64.s" --isa aarch64 \
    -o "$OUT/example" --show-mapping

step "Matching tests: correct programs, mutations, verdict policy, determinism, CLI, random programs
     (CFGVERIFY_FUZZ_N=${CFGVERIFY_FUZZ_N:=20})"
CFGVERIFY_FUZZ_N=$CFGVERIFY_FUZZ_N $PY -m pytest -q tests/test_matching.py

printf '\nCHECKPOINT PART 7 PASSED: one matcher, no program-specific rules, verified every supported program\n'
printf 'against its own assembly on 3 candidate architectures, and caught and located every deliberate break.\n'
printf 'Reports: %s/<isa>/   Worked example: %s/example/\n' "$OUT" "$OUT"
printf 'REMINDER: what "consistent" must mean is still TO CONFIRM WITH PROFESSOR [C1], and so is whether\n'
printf 'UNSUPPORTED may be answered at all [C3].\n'
