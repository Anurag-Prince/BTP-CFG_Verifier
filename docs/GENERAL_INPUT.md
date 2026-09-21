# Part 8: general input

The requirement is a negative one, and it is the one that decides whether this is a tool or a demo:

> The final system must not be hard-coded for the initial 10 programs. Adding a new valid supported C/HLL
> program must not require modifying the CFG generator, the normalizer or the matcher.

So the ten programs in `regression/c/` are **regression tests**. They are not the supported input set, and
nothing in `src/cfgverify/` knows they exist.

```bash
python main.py --source program.c --assembly program.s
```

That is the whole interface. It works on a file the system has never seen, and it is the same command that
runs the regression set.

---

## 1. The pipeline

Eight steps, in the order the brief lists them, all in `src/cfgverify/pipeline.py`:

```
1. read the C source          5. read the assembly
2. generate/read the LLVM IR  6. build the assembly CFG
3. extract the basic blocks   7. normalize both CFGs (the same rules on each)
4. build the HLL CFG          8. match, decide and report
```

`python main.py --steps` prints them. Nothing on this path branches on a file name, a function name or a
program — a failure is reported as *"step 'read the assembly' failed: …"*, so an unusable input always says
which step stopped being able to proceed.

### What you get back

| | |
|---|---|
| on the terminal | a per-function table, every finding with its C and assembly line numbers, and the verdict |
| `<name>.md` | a report a human can read, or hand in |
| `<name>.verdict.json` | the evidence record (`cfgverify.verdict/1`) for a script or the explainer |
| `<function>.hll.cfg.json`, `.asm.cfg.json` | the raw CFGs, before normalization |
| `<function>.hll.norm.cfg.json`, `.asm.norm.cfg.json` | the normalized CFGs that were actually compared |
| `<function>.hll.rules.json`, `.asm.rules.json` | every normalization rule that fired, and on which blocks |
| `<name>.ll` | the LLVM IR, when the input was C |

Every intermediate step is written down. A reviewer can check any stage by hand, which is what makes the
verdict reviewable rather than merely printed.

### Exit codes

`0` MATCH · `2` MISMATCH · `3` UNSUPPORTED · `1` the input could not be processed.

### Options

```bash
python main.py --source p.c --assembly p.s                 # the architecture is detected and announced
python main.py --source p.c --assembly p.s --isa aarch64   # or named explicitly
python main.py --source p.c --assembly p.s --explain       # add a plain-English explanation
python main.py --source p.c --assembly p.s --json          # the evidence record on stdout
python main.py --batch dir/                                # every x.c that has a matching x.s
python main.py --source p.c --assembly p.s --require-conditions   # the strict reading of [C1]
python main.py --source p.c --assembly p.s --strict               # never answer UNSUPPORTED [C3]
```

`--batch` pairs files by stem. That is a convention of the *caller*; the pipeline itself is always given two
explicit paths and never guesses which assembly belongs to which source.

## 2. The explanation layer

Optional, and the last thing that happens. The verdict is already decided when it is called — rule **R5**,
*AI never decides correctness, it only explains evidence*.

**`template()` is the default.** A deterministic explanation built from the evidence record alone: no model,
no network, no configuration, no key in the repo. The system is complete without any AI at all. Every
sentence it writes restates a field of the record.

**`model()` is optional**, used only when a caller passes a provider in. Four guardrails:

1. **Input is the frozen evidence only** — verdicts, per-function counts, findings. No tools, no re-running
   the analysis, and no file paths, target triples or ISA names: those are not evidence about the program,
   and a model has no business being told them.
2. **The deterministic verdict is printed first**, above the prose, which is labelled *non-authoritative*.
3. **Post-check before it is shown.** `check()` rejects text that cites a finding id that does not exist,
   names a verdict other than the one decided, claims a kind of finding that was not reported, or describes a
   difference when the verdict is MATCH. A rejected answer is replaced by the template — silently to the
   reader, loudly in `Explanation.rejected`.
4. **A provider that crashes changes nothing.** The template is used and the run continues.

A test asserts from the source that the explainer never assigns to the evidence record and never calls the
verdict logic. Which provider, and whether an AI explanation is wanted at all, is **[E1]**.

## 3. The checkpoint

> **Checkpoint: adding a new supported program must not require modifying the CFG generator, the normalizer
> or the matcher. Later evaluation should use many additional unseen programs.**

`bash scripts/checkpoint_part8.sh` is built to be able to fail:

1. take the sha256 of all 18 implementation files **before** anything runs;
2. grep them for the name of every program and every function in the repo;
3. run the full pipeline over programs the implementation has never seen, on every candidate architecture;
4. take the checksums **again** and require them to be identical.

### The unseen corpus

`examples/c/unseen/` holds programs written *after* Parts 3–7 were finished. They deliberately use things the
regression set does not have:

| Program | What is new about it |
|---|---|
| `matrix_walk.c` | two-deep nesting with an early `return` out of the innermost loop |
| `digits.c` | sequential loops; a `continue` inside a `do`-`while`; a function calling another in this file |
| `search.c` | a loop with two exits; a helper called from inside a condition |
| `straight.c` | no control flow at all — one basic block per function |
| `recurse.c` | recursion, and mutual recursion between two functions |
| `ranges.c` | a five-way chained `else if` |
| `accumulate.c` | an inner loop whose bound depends on the outer index; **three-deep** nesting; `break` in an outer loop |

A test (`test_the_unseen_corpus_uses_constructs_the_regression_set_does_not`) checks that this is true, because
verifying copies of the regression programs would prove nothing.

### Result

| Input | x86-64 | AArch64 | RISC-V |
|---|---|---|---|
| 7 hand-written unseen programs (18 functions) | **MATCH** | **MATCH** | **MATCH** |
| 25 freshly generated programs | **MATCH** | **MATCH** | **MATCH** |
| **32 programs × 3 architectures = 96 verifications** | | **all MATCH** | |
| implementation checksums afterwards | | **identical** | |

Two of the functions verified here are larger than anything in the regression set: `count_triples` is 16
basic blocks, 20 edges, 5 decisions and 3 nested loops, and it matched on all three architectures with no
findings at all.

## 4. What this does and does not prove

**It does prove** that the analysis is general over the supported subset: the same code verified 96
program/architecture pairs it had never seen, and no file changed.

**It does not prove** that every C program will work. The supported subset is **[B3]** — `switch`, `goto`,
`&&`/`||` short-circuiting, function pointers and floating-point conditions are not in Phase A. An input
outside it is not silently mishandled: the construct is named in an `UNSUPPORTED_CONSTRUCT` finding and the
verdict is `UNSUPPORTED`, which is the honest answer and is itself **[C3]**.

**It does not prove the matcher is right**, only that it is general. That is what Part 7's mutation tests are
for, and they run against this same pipeline.

## 5. Findings

* **F1 — a negative requirement needs a negative test.** "It is not hard-coded" cannot be shown by running
  the programs it was built with. Checksums taken before and after, plus a grep of the implementation for
  every name in the corpus, can fail; a passing test suite cannot.
* **F2 — the word collision is real.** The grep in (2) fires on `sign`, which is a function in `if_else.c`
  *and* an x86/AArch64 condition-code name, and on `mix` and `classify`, which are ordinary English. They are
  excluded by name with the reason written down, rather than by weakening the check.
* **F3 — a prompt leaks more than you mean it to.** The first version of `build_prompt()` passed the
  per-function summary straight through, which included the absolute path of the C file and the build
  directory. The evidence a model needs is counts and findings; everything else is now stripped.
* **F4 — every artefact, or none.** Writing only the final report would make the verdict unreviewable. The
  pipeline writes the IR, both raw CFGs, both normalized CFGs and both rule logs, so any step can be checked
  by hand against the file it came from.
