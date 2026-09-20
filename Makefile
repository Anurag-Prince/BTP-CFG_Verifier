# Shortcuts. Run inside WSL/Ubuntu from the repo root.
PY ?= .venv/bin/python

.PHONY: setup env checkpoint test clean

setup:       ## install system packages + create .venv
	bash scripts/setup_wsl.sh

env:         ## show and record toolchain versions
	$(PY) -m cfgverify env

checkpoint:  ## Part 1 checkpoint: C -> LLVM IR end to end
	bash scripts/checkpoint_part1.sh

test:        ## run the test suite
	$(PY) -m pytest

clean:       ## delete generated files
	rm -rf build .pytest_cache
