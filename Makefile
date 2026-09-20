# Shortcuts. Run inside WSL/Ubuntu from the repo root.
PY ?= .venv/bin/python

.PHONY: setup env checkpoint regression checkpoint3 checkpoint4 checkpoint5 docs test clean

setup:       ## install system packages + create .venv
	bash scripts/setup_wsl.sh

env:         ## show and record toolchain versions
	$(PY) -m cfgverify env

checkpoint:  ## Part 1 checkpoint: C -> LLVM IR end to end
	bash scripts/checkpoint_part1.sh

regression:  ## Part 2 check: expected CFGs vs LLVM
	bash scripts/check_part2.sh

checkpoint3: ## Part 3 checkpoint: new C files -> IR -> blocks/edges (reader unchanged)
	bash scripts/checkpoint_part3.sh

checkpoint4: ## Part 4 checkpoint: C files -> HLL CFGs (JSON/SVG/gallery), all checked
	bash scripts/checkpoint_part4.sh

checkpoint5: ## Part 5 checkpoint: C -> assembly (3 candidate ISAs, 2 compilers, 2 syntaxes) -> ASM CFGs
	bash scripts/checkpoint_part5.sh

docs:        ## redraw expected CFGs and refresh docs/CONTROL_FLOW_FUNDAMENTALS.md
	$(PY) -m cfgverify expected-cfgs

test:        ## run the test suite
	$(PY) -m pytest

clean:       ## delete generated files
	rm -rf build .pytest_cache
