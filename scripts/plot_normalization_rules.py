import csv
from pathlib import Path
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]

SUMMARY = ROOT / "build" / "experiment" / "summary.csv"
OUT = ROOT / "build" / "experiment" / "normalization_rules_usage.png"

# Read experiment summary
with open(SUMMARY, newline="") as f:
    rows = list(csv.DictReader(f))

rules = [f"N{i}" for i in range(1, 9)]

# Add rule counts across the evaluated ISAs.
# RISC-V is excluded because it was not evaluated locally.
totals = {
    rule: sum(
        int(row[rule])
        for row in rows
        if row["isa"] != "riscv"
    )
    for rule in rules
}

names = list(totals.keys())
values = list(totals.values())

plt.figure(figsize=(10, 6))

bars = plt.bar(names, values)

plt.xlabel("Normalization Rule")
plt.ylabel("Number of Applications")
plt.title("Normalization Rules Applied Across the Experiment")

# Show exact value above each bar
for bar, value in zip(bars, values):
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height(),
        str(value),
        ha="center",
        va="bottom"
    )

plt.tight_layout()

plt.savefig(
    OUT,
    dpi=300,
    bbox_inches="tight"
)

plt.close()

print(f"Graph saved to: {OUT}")
print()
print("Normalization rule counts:")

for rule, count in totals.items():
    print(f"{rule}: {count}")