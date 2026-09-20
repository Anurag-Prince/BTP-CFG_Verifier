# Part 1: Development environment

**Goal:** a reproducible toolchain in which a C program can be compiled to LLVM IR, and the IR can be loaded into
Python graph tools and drawn.
**Checkpoint:** `bash scripts/checkpoint_part1.sh` prints `CHECKPOINT PART 1 PASSED`.

## Platform decision

| Item | Choice | Why |
|---|---|---|
| Host | Windows x64 | The student's machine |
| Linux layer | **WSL2 + Ubuntu 24.04 LTS** | Clang/LLVM, Graphviz and the GNU tools install with one `apt` command. It is the standard platform for compiler work, and it avoids the MSVC/MinGW header setup that native Windows Clang needs. The distro is pinned to 24.04 so the apt versions stay fixed. |
| Repo location | `C:\Users\<you>\Documents\btp-cfg-verifier`, which is `/mnt/c/Users/<you>/Documents/btp-cfg-verifier` inside WSL | Visible from both Windows and WSL |

Whether a Linux/WSL setup is acceptable for the final demo is **TO CONFIRM WITH PROFESSOR** ([F3]).
The development platform does **not** decide which assembly ISA is verified ([A1]).

## Tools and why each one is needed

| Tool | Installed by | Role in this project | Check with |
|---|---|---|---|
| **WSL2** | Windows (`wsl --install`) | Runs a real Linux kernel on Windows | `wsl --list --verbose` (PowerShell) |
| **Ubuntu 24.04** | WSL | The Linux distribution; `apt` provides fixed tool versions | `cat /etc/os-release` |
| **build-essential** (gcc, make, libc6-dev, binutils `as`/`ld`) | apt | C headers, the C library and the linker that Clang needs to build native programs. GCC is a second compiler, which may matter if the professor's assembly comes from GCC ([A3]). | `gcc --version`, `as --version`, `make --version` |
| **clang** | apt `clang` | The C front end: **C → LLVM IR** (`-S -emit-llvm`). Also builds native test programs. | `clang --version` |
| **LLVM tools** (`opt`, `llc`, `llvm-config`, `llvm-as`, `llvm-dis`) | apt `llvm` | `opt -passes=dot-cfg` exports LLVM's own CFG, our visualization and future oracle. `llc` turns IR into assembly for any of its registered targets, which will help once the ISA is confirmed. | `opt --version`, `llc --version` |
| **Graphviz** (`dot`) | apt `graphviz` | Draws DOT graphs as SVG/PNG | `dot -V` |
| **Python 3.12 + venv + pip** | apt `python3 python3-venv python3-pip` | Implementation language. `.venv` isolates the project's packages. | `python3 --version` |
| **git** | apt `git` | Version control | `git --version` |

### Python packages (`requirements.txt`, pinned)

| Package | Version | Role |
|---|---|---|
| `llvmlite` | 0.49.0 | Python bindings to LLVM. It **reads `.ll` files** and will be used by the IR reader in Part 2. It bundles its own LLVM (22.1), which reads IR from Clang 18 without problems. |
| `networkx` | 3.6.1 | Graph data structures and algorithms: dominators and loops, graph isomorphism (VF2) for the matcher |
| `pydot` | 4.0.1 | Parses DOT files, such as LLVM's `dot-cfg` output, into Python |
| `graphviz` | 0.21 | Builds and renders our own CFG diagrams from Python (calls the `dot` binary) |
| `pytest` | 9.1.1 | Test runner |

## Reference versions

These versions were verified by running `setup_wsl.sh` and `checkpoint_part1.sh` from a clean copy on Ubuntu 24.04.4
(x86_64). Your own run records its versions in `build/toolchain.json`. Compare the two if results ever differ.

| Tool | Version |
|---|---|
| clang / opt / llc / llvm-config | 18.1.3 (Ubuntu 24.04 apt) |
| Graphviz `dot` | 2.43.0 |
| gcc | 13.3.0 |
| GNU as (binutils) | 2.42 |
| make | 4.3 |
| git | 2.43.0 |
| Python | 3.12.3 |
| llvmlite (bundled LLVM) | 0.49.0 (22.1.0) |

LLVM IR text changes between LLVM releases. The exact Clang version is therefore part of every result, and whether
the professor requires a particular version is an open question ([A6]).

## Canonical C → LLVM IR command

```bash
clang -S -emit-llvm -O0 -fno-discard-value-names examples/c/hello_cfg.c -o build/hello_cfg.ll
```

| Flag | Meaning |
|---|---|
| `-S -emit-llvm` | Write **textual** LLVM IR (`.ll`) instead of an object file |
| `-O0` | No optimization, so the IR keeps the source's if/loop structure |
| `-fno-discard-value-names` | Keep readable block names (`for.cond`, `if.then`); otherwise they become `%1`, `%2`, … |
| *(no `--target`)* | Host default (`x86_64-pc-linux-gnu` on WSL). **This is a placeholder only.** The IR target must match the assembly's target once that is confirmed ([A1], [A7]). |
| *(no `-std`)* | Clang's default C standard. The required standard is TO CONFIRM ([B1]). |

These flags are defined once, in `src/cfgverify/config.py`.

## What the checkpoint proves

| Step | Proves |
|---|---|
| 1. `cfgverify env` | Every required tool and package is present. Versions are recorded, and mismatches between clang, opt and llvmlite produce warnings. |
| 2. Native build and run of `hello_cfg.c` | The compiler, C headers, libc and linker all work. The program prints known values. |
| 3. `cfgverify emit-ir` | **C → LLVM IR works.** llvmlite can parse and verify the IR that this Clang produced. |
| 4. Print one IR function | You can see the basic blocks (`entry`, `if.then`, `land.lhs.true`, …) |
| 5. `cfgverify llvm-cfg` | LLVM's CFG loads into networkx correctly (one node per block) and renders to SVG |
| 6. `pytest` | Repeatable checks. For every function, the number of two-way branches equals the number of decisions counted by hand in the C source. |

Expected numbers for `hello_cfg.c` (Clang 18, `-O0`):

| function | blocks | edges | 2-way branches | exit blocks | cyclomatic |
|---|---|---|---|---|---|
| classify | 7 | 9 | 3 | 1 | 4 |
| count_digits | 6 | 7 | 2 | 1 | 3 |
| main | 1 | 0 | 0 | 1 | 1 |
| sum_even_upto | 10 | 12 | 3 | 1 | 4 |
