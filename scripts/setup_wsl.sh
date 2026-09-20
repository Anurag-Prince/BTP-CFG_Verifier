#!/usr/bin/env bash
# Part 1 environment setup for Ubuntu 24.04 (WSL2 or native).
#   Installs: C toolchain (build-essential), Clang + LLVM tools, Graphviz, Python venv support, git
#   Creates : .venv with the pinned Python packages, plus this project installed in editable mode
# Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

APT_PACKAGES=(build-essential clang llvm graphviz python3 python3-venv python3-pip git make)

echo "==> [1/4] Installing system packages: ${APT_PACKAGES[*]}"
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}"

PYTHON="${PYTHON:-python3}"
echo "==> [2/4] Checking Python ($("$PYTHON" --version))"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python >= 3.11 is required")'

echo "==> [3/4] Creating virtual environment in .venv"
if [ ! -x .venv/bin/python ]; then
    # On the Windows filesystem (/mnt/c) symlinks can be refused; fall back to copying the interpreter.
    "$PYTHON" -m venv .venv || { rm -rf .venv; "$PYTHON" -m venv --copies .venv; }
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .

echo "==> [4/4] Recording toolchain versions"
.venv/bin/python -m cfgverify env

echo
echo "Setup complete. Next:"
echo "  source .venv/bin/activate"
echo "  bash scripts/checkpoint_part1.sh"
