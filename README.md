# btp-cfg-verifier

This B.Tech project checks whether an **assembly program has the same control flow as the C (HLL) program it
claims to implement**. It builds a control-flow graph (CFG) for each side, normalizes both, and matches them:

```
C ──clang──► LLVM IR ──► basic blocks ──► HLL CFG ──► normalize ──┐
                                                                  ├──► match ──► MATCH / MISMATCH ──► AI explanation
Assembly ─────────────► basic blocks ──► ASM CFG ──► normalize ──┘              (deterministic)      (advisory only)
```

The deterministic matcher is the only authority on the verdict. The AI layer only explains evidence the matcher
has already produced.

| Part | Status |
|---|---|
| **Part 0**: project understanding and design | Done (draft v0.1). See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Open requirements | Listed in [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md), each marked **TO CONFIRM WITH PROFESSOR** |
| **Part 1**: development environment, C → LLVM IR checkpoint | Done. See below and [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) |
| Part 2+ | Not started. Proposed roadmap in ARCHITECTURE §12 |

> The target assembly architecture is **not yet decided** (TO CONFIRM WITH PROFESSOR). Nothing in this repo assumes
> x86-64. The host's default target triple is used only to generate IR for the Part 1 smoke test.

---

## Quick start (Windows + WSL2 Ubuntu 24.04)

### 1. Install WSL2 with Ubuntu 24.04 (one time, on Windows)
Open **PowerShell as Administrator** and run:
```powershell
wsl --install -d Ubuntu-24.04
```
Reboot if asked. Then open "Ubuntu 24.04" from the Start menu and create your Linux username and password.
If WSL is already installed, check with `wsl --list --verbose` that the distro shows `VERSION 2`.

### 2. Go to the repo from inside Ubuntu
Windows drives are mounted under `/mnt/`. For example `C:\Users\<you>\Documents\btp-cfg-verifier` becomes:
```bash
cd /mnt/c/Users/<you>/Documents/btp-cfg-verifier
```

### 3. Set up the toolchain and Python environment
```bash
bash scripts/setup_wsl.sh        # asks for your Linux sudo password; safe to re-run
source .venv/bin/activate
```
This installs clang/LLVM, Graphviz, the C build tools and Python venv support with `apt`. It then creates `.venv` with
the pinned packages from `requirements.txt` and installs this project in editable mode (the `cfgverify` command).

### 4. Run the Part 1 checkpoint
```bash
bash scripts/checkpoint_part1.sh     # or: make checkpoint
```
It ends with `CHECKPOINT PART 1 PASSED: C -> LLVM IR works on this machine.` To see a CFG, open
`build\llvm_cfg\classify.svg` in a browser from Windows, or run `explorer.exe build/llvm_cfg` from WSL.

---

## Commands (Part 1)

| Command | What it does |
|---|---|
| `cfgverify env` | Prints every tool and package version and records them in `build/toolchain.json` |
| `cfgverify emit-ir FILE.c [-o OUT.ll] [--target TRIPLE]` | C → LLVM IR with the project's canonical flags (`-S -emit-llvm -O0 -fno-discard-value-names`) |
| `cfgverify llvm-cfg FILE.ll [-o DIR]` | LLVM's own per-function CFG (`opt -passes=dot-cfg`) → DOT + SVG + a networkx summary |
| `make test` / `pytest` | Environment and C → IR tests |

`python -m cfgverify ...` works the same way as `cfgverify ...`.

## Repository layout

```
docs/              ARCHITECTURE.md (Part 0), OPEN_QUESTIONS.md (for the professor), ENVIRONMENT.md (Part 1)
scripts/           setup_wsl.sh, checkpoint_part1.sh
examples/c/        hello_cfg.c (smoke-test program)
src/cfgverify/     the package; each module's docstring states its responsibility and status
  hll/             C → IR → HLL CFG        (frontend.py and llvm_view.py are implemented; others are stubs)
  asm/             assembly → ASM CFG      (stubs; blocked until the ISA is confirmed)
  normalize/ match/ report/ explain/      (stubs)
tests/             pytest suite
build/             generated output (git-ignored)
```

## Git workflow (proposal)
- `main` always passes `make checkpoint` and `make test`.
- Do each part on a branch (`part-2-hll-cfg`, …) and merge it when that part's checkpoint passes. Tag it, for example
  `git tag part1-checkpoint`.
- To publish on GitHub, create an **empty private** repository, then run
  `git remote add origin <url> && git push -u origin main`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `bash\r: No such file or directory` or `bad interpreter` | A script got Windows line endings. Run `sed -i 's/\r$//' scripts/*.sh`. (`.gitattributes` prevents this for files that come from git.) |
| `git status` shows every file as modified (mode changes) | The Windows filesystem reports all files as executable. Run `git config core.fileMode false`. |
| `fatal: detected dubious ownership in repository` | Run `git config --global --add safe.directory "$(pwd)"` |
| `ensurepip is not available` when creating the venv | Run `sudo apt install python3-venv` (the setup script does this). |
| Everything feels slow | The repo is on the Windows filesystem. Optionally work on a clone inside WSL: `git clone /mnt/c/Users/<you>/Documents/btp-cfg-verifier ~/btp-cfg-verifier` |
| llvmlite fails to parse IR | Your clang is newer than llvmlite's bundled LLVM. `cfgverify env` warns about this. |
