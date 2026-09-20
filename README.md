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
| **Part 2**: C/HLL control-flow fundamentals + regression programs | Done. See [`docs/CONTROL_FLOW_FUNDAMENTALS.md`](docs/CONTROL_FLOW_FUNDAMENTALS.md) and [`regression/`](regression) |
| **Part 3**: LLVM IR generation and parsing (IR reader) | Done. See [`docs/LLVM_IR_GUIDE.md`](docs/LLVM_IR_GUIDE.md) |
| **Part 4**: HLL CFG generator (JSON, NetworkX, Graphviz) | Done. See [`docs/HLL_CFG.md`](docs/HLL_CFG.md) |
| **Part 5**: Assembly CFG generator (ISA-neutral reader + ISA plug-ins) | Done. See [`docs/ASM_CFG.md`](docs/ASM_CFG.md) |
| Later parts | Not started. Roadmap in ARCHITECTURE §12 |

> The target assembly architecture is **not yet decided** (TO CONFIRM WITH PROFESSOR). Nothing in this repo assumes
> x86-64. The host's default target triple is used only to generate IR for the Part 1 smoke test. Part 5 ships
> three *candidate* ISA plug-ins — x86-64 (AT&T and Intel), AArch64 and RISC-V — with **no default**: `--isa` must
> be given on every `asm-cfg` run. A candidate is not a decision; see [A1]/[A2] in `docs/OPEN_QUESTIONS.md`.

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

### 5. Run the Part 2 check
```bash
bash scripts/check_part2.sh          # or: make regression
```
This checks the hand-derived expected CFG of every regression function against LLVM's own CFG. It ends with
`PART 2 CHECK PASSED`. The study notes with all expected CFG diagrams are in `docs/CONTROL_FLOW_FUNDAMENTALS.md`.

### 6. Run the Part 3 checkpoint
```bash
bash scripts/checkpoint_part3.sh                 # the new programs in examples/c/part3/
bash scripts/checkpoint_part3.sh my_program.c    # or any C file of your own
```
It compiles each file to LLVM IR and prints every function's blocks, terminators, successors and predecessors. It
cross-checks the result against LLVM itself, then runs the reader tests, including 100 random C programs per mode.
It ends with `CHECKPOINT PART 3 PASSED`. This takes a few minutes.

### 7. Run the Part 4 checkpoint
```bash
bash scripts/setup_wsl.sh                        # once, to install jsonschema (added in Part 4)
bash scripts/checkpoint_part4.sh                 # all C files in the repo
bash scripts/checkpoint_part4.sh my_program.c    # or your own files
```
It builds an HLL CFG for every function (JSON, SVG and an HTML gallery in `build/part4/`) and checks each one.
It ends with `CHECKPOINT PART 4 PASSED`. Open the gallery with `explorer.exe build/part4/index.html`.

### 8. Run the Part 5 checkpoint
```bash
bash scripts/checkpoint_part5.sh                 # all C files in the repo, every candidate ISA
bash scripts/checkpoint_part5.sh my_program.c    # or your own files
```
It compiles every program to **x86-64, AArch64 and RISC-V** assembly (plus GCC output and Intel syntax), reads the
three hand-written files in `regression/asm/` with `--isa auto`, and builds a CFG for each with the *same*
generator — only the `--isa` argument changes. Each CFG is checked against the compiler's own machine-block
marks, the assembler and the C it came from. It ends with `CHECKPOINT PART 5 PASSED` and a reminder that the
architecture to verify is still TO CONFIRM WITH PROFESSOR. Gallery: `explorer.exe build/part5/index.html`.

---

## Commands

| Command | What it does |
|---|---|
| `cfgverify env` | Prints every tool and package version and records them in `build/toolchain.json` |
| `cfgverify emit-ir FILE.c [-o OUT.ll] [--target TRIPLE]` | C → LLVM IR with the project's canonical flags (`-S -emit-llvm -O0 -fno-discard-value-names`) |
| `cfgverify llvm-cfg FILE.ll [-o DIR]` | LLVM's own per-function CFG (`opt -passes=dot-cfg`) → DOT + SVG + a networkx summary |
| `cfgverify expected-cfgs [--check]` | Part 2: redraws `docs/cfg/*.svg` from `regression/expected/*.toml` and refreshes the generated parts of the study doc |
| `cfgverify parse-ir FILE.c\|FILE.ll [-g] [-i] [-f FN] [--json OUT] [--crosscheck]` | Part 3: blocks, instructions, terminators, successors, predecessors and edges of every function. `--crosscheck` compares them with LLVM |
| `cfgverify hll-cfg FILE... [-o DIR] [--detail ops\|ir\|none] [--check]` | Part 4: HLL CFG per function → `DIR/<file>.<fn>.cfg.json` + `.svg` + `index.html`. `--check` validates each one |
| `cfgverify asm-cfg FILE.s... --isa ISA\|auto [-o DIR] [--c-source F.c] [--check]` | Part 5: assembly CFG per function → `DIR/<file>.<isa>.<fn>.cfg.json` + `.svg` + `index.html`. **`--isa` is required** (`auto` detects and prints its decision) |
| `make test` / `pytest` | All tests: environment, C → IR, Part 2 regression CFGs, Part 3 IR reader, Part 4 HLL CFGs, Part 5 assembly CFGs (about 3 minutes) |

`python -m cfgverify ...` works the same way as `cfgverify ...`.

## Repository layout

```
docs/              ARCHITECTURE.md (Part 0), OPEN_QUESTIONS.md (for the professor), ENVIRONMENT.md (Part 1),
                   CONTROL_FLOW_FUNDAMENTALS.md + cfg/*.svg (Part 2), LLVM_IR_GUIDE.md (Part 3), HLL_CFG.md (Part 4),
                   ASM_CFG.md (Part 5)
scripts/           setup_wsl.sh, checkpoint_part1.sh, check_part2.sh, checkpoint_part3.sh, checkpoint_part4.sh,
                   checkpoint_part5.sh
examples/c/        hello_cfg.c (smoke-test program), part3/ (Part 3 checkpoint inputs)
regression/        c/*.c regression programs + expected/*.toml expected CFGs (Part 2),
                   asm/*.s hand-written assembly with no compiler directives (Part 5)
src/cfgverify/     the package; each module's docstring states its responsibility and status
  graph_utils.py   dominators, back edges, natural loops (Part 2)
  expected.py      load, draw and document expected CFGs (Part 2)
  model/           CFG data model: ops.py, cfg.py (+ cfg.schema.json), cfg_dot.py (Part 4)
  hll/             C → IR → HLL CFG        (frontend, llvm_view, ir_reader, ir_crosscheck, ir_ops, cfg_builder, cfg_checks)
  asm/             assembly → ASM CFG      (reader, cfg_builder, cfg_checks, isa/ plug-ins; Part 5)
  normalize/ match/ report/ explain/      (stubs)
tests/             pytest suite
build/             generated output (git-ignored)
```

## Git workflow (proposal)
- `main` always passes `make checkpoint` and `make test`.
- Do each part on a branch (`part-3`, …) and merge it when that part's checkpoint passes. Tag it, for example
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
