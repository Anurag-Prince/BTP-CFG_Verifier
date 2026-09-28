import csv
from pathlib import Path
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]

SUMMARY = ROOT / "build" / "experiment" / "summary.csv"
OUT = ROOT / "build" / "experiment" / "cfg_normalization_results.png"

rows = []

with open(SUMMARY, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

# Ignore RISC-V because the local Clang installation
# does not have a RISC-V backend.
rows = [row for row in rows if row["isa"] != "riscv"]

isas = [row["isa"] for row in rows]

before = [
    float(row["raw_agreement_rate"]) * 100
    for row in rows
]

after = [
    float(row["normalized_agreement_rate"]) * 100
    for row in rows
]

x = range(len(isas))
width = 0.35

plt.figure(figsize=(9, 6))

plt.bar(
    [i - width / 2 for i in x],
    before,
    width,
    label="Before normalization"
)

plt.bar(
    [i + width / 2 for i in x],
    after,
    width,
    label="After normalization"
)

plt.xticks(list(x), isas)

plt.ylabel("Structural agreement (%)")
plt.xlabel("ISA")
plt.title("Effect of CFG Normalization")

plt.ylim(0, 100)
plt.legend()

plt.tight_layout()

plt.savefig(
    OUT,
    dpi=300,
    bbox_inches="tight"
)

plt.close()

print(f"Graph saved to: {OUT}")