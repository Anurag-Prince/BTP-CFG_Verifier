import re
from pathlib import Path

src = Path("demo/if_else.s")
dst = Path("demo/wrong_branch.s")

text = src.read_text()

labels = re.findall(r"^\.LBB\d+_\d+:", text, re.M)

first = labels[0][:-1]
second = labels[1][:-1]

out = []
swapped = False

for line in text.splitlines():
    stripped = line.strip()

    if not swapped and stripped.endswith(first) and not stripped.startswith("."):
        out.append(line.replace(first, second))
        swapped = True
    else:
        out.append(line)

assert swapped, "Could not find branch target"

dst.write_text("\n".join(out) + "\n")

print(f"Created {dst}")
