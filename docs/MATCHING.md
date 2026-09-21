# Part 7: CFG matching and verification

This is the part that answers the question the project exists to ask: **does this assembly have the same
control flow as this C program?** Everything before it was preparation — the CFGs, the common vocabulary, the
normalization. This part compares the two normalized graphs, says MATCH or MISMATCH, and, when they differ,
says *where* and *what*.

```
C file ──► Parts 3-4 ──► HLL CFG  ──► Part 6 ──► normalized ─┐
                                                              ├──► match/  ──► findings ──► report/verdict.py
.s file ──► Part 5   ──► ASM CFG  ──► Part 6 ──► normalized ─┘      L0-L4                        │
                                                                                                  ▼
                                                                           MATCH | MISMATCH | UNSUPPORTED
```

```bash
cfgverify verify regression/c/while.c build/while.aarch64.s --isa aarch64 -o build/part7
bash scripts/checkpoint_part7.sh          # every program, every candidate ISA, plus the mutation tests
```

Exit codes: `0` MATCH, `2` MISMATCH, `3` UNSUPPORTED — so the tool can be used in a script or a CI job.

---

## 1. The five levels

Cheapest first. Each level either rejects with a located reason or hands the next one more information.

| Level | What it does | What it costs |
|---|---|---|
| **L0** function pairing | pair functions by symbol name; unpaired ones become `FUNCTION_MISSING` / `FUNCTION_EXTRA` **[B4]** | nothing |
| **L1** invariants | blocks, edges, decisions, cyclomatic complexity, loops, max nesting depth, exits, return paths, back edges, unsupported constructs | nothing |
| **L2** exact structure | graph isomorphism (networkx VF2) with the entry pinned to the entry and every block's *role* required to agree — this is what decides MATCH | small; these CFGs have under 15 blocks |
| **L3** anchors | when several isomorphisms fit a symmetric graph, choose the one whose blocks compute the most similar things — a **tie-breaker, never a filter** | small |
| **L4** branch conditions | for every mapped decision, compare the comparable condition key from Part 6 | nothing |

A block's **role** at L2 is: out-degree, in-degree, terminator class, entry/exit, loop header, loop depth,
the kinds of its outgoing edges, and how many are back edges. Names, labels, registers, instruction text and
line numbers are never part of it.

### Three things L2 deliberately does *not* require

Each of these was found by running the matcher against real compiler output, and each one, if required,
turns a correct program into a false MISMATCH:

1. **`T` and `F` need not correspond.** A compiler may emit the opposite test and swap the arms:
   `for (i = 1; i <= n; i++)` becomes `jg` over the *false* arm on x86-64. Both are folded to `cond`, which
   is what normalization rule N6 is for. **Without this, 51 of 69 correct function/ISA pairs failed.**
2. **The condition itself is not part of the shape.** A compiler may emit `x >= 2` for `x > 1`, use an
   unsigned compare for a signed test, or test the sign bit instead of comparing with zero (Part 6, finding
   F3). Those are rewrites of the *test*, not of the *control flow*. L4 compares them separately and reports
   a disagreement as its own finding. **Without this, a further 13 correct pairs failed.**
3. **Block content need not be equal.** The assembly legitimately does more work than the IR: a stack
   prologue is `SUBTRACT STORE STORE ASSIGN STORE` where the C entry block is `STORE STORE STORE`, and
   AArch64 computes a comparison with `subs`, which is a `SUBTRACT` where the IR has a `COMPARE`. L3 scores
   *containment* — the assembly may do more, never less — and uses it only to choose between mappings that
   are already valid.

## 2. When the graphs are not isomorphic

Answering "MISMATCH" and stopping would be useless. `partial_mapping()` builds the best mapping it can, so
that the difference can be *localized* rather than smeared over the whole function. Four passes, each weaker
than the last, all deterministic:

1. **Colour refinement** (1-WL): pair blocks whose refined colour is unique on both sides.
2. **Anchors**: pair the entries, then blocks that compute the same thing at the same distance from the entry.
3. **Growth**: extend along edges from what is already paired.
4. **Best remaining candidate** by content containment, then nearness in the flow.

A partial mapping is never presented as a match — `MATCH` requires a *positive* isomorphism result with every
block paired. It exists only so the findings can name blocks on both sides.

This matters more than it sounds. Before the anchor and growth passes existed, a single extra decision in
`count_primes` produced 5 `MISSING_BLOCK` and 6 `EXTRA_BLOCK` findings — technically true, useless to read.
With them, the same input produces findings that point at the two blocks that actually differ.

## 3. The findings

Every finding names the blocks on both sides, the raw labels they came from (`while.cond`, `.LBB0_2`), the
assembly line numbers, and the C source lines when `-g` provided them.

| Kind | Produced when |
|---|---|
| `MISSING_BLOCK` | a normalized HLL block has no counterpart |
| `EXTRA_BLOCK` | a normalized assembly block has no counterpart |
| `MISSING_EDGE` | both endpoints are mapped, the HLL has the edge, the assembly does not |
| `EXTRA_EDGE` | both endpoints are mapped, the assembly has the edge, the HLL does not |
| `WRONG_BRANCH` | a mapped block branches a different number of ways, or its arms lead elsewhere |
| `WRONG_BRANCH_CONDITION` | the arms line up but the comparable condition key differs (L4) |
| `MISSING_LOOP_BACK_EDGE` | an HLL back edge has no counterpart: the assembly may not loop |
| `UNEXPECTED_PATH` | the assembly can execute a sequence of semantic operations the C program cannot |
| `MISSING_RETURN` | a return path of the C program does not return in the assembly |
| `STRUCTURAL_MISMATCH` | an L1 invariant disagrees and nothing above explains it; the invariant is named |

Plus four the earlier parts need: `FUNCTION_MISSING`, `FUNCTION_EXTRA` **[B4]**, `UNSUPPORTED_CONSTRUCT`
**[C3]**, and `AMBIGUOUS_MAPPING` (several mappings fit equally well — reported, never guessed).

`UNEXPECTED_PATH` reuses Part 6's path machinery directly: the acyclic view of each CFG, with back edges
redirected to a "round the loop again" sink, reduced to sequences of semantic operations. A sequence the
assembly can perform and the C program cannot is a behaviour that was added by whatever produced the
assembly.

### A real one

Redirecting the inner back edge of `nested_loop.c` in the assembly:

```
MISSING_LOOP_BACK_EDGE   the loop back edge B4 -> B3 has no counterpart:
                         assembly block B3 does not go to B5
                         [for.body3+for.inc+for.cond1 | .Ltmp7+.Ltmp11]
                         C line(s): 12, 13, 14
                         assembly line(s): 52, 53, 54, 58, 59, 60, +4 more
```

## 4. The verdict

`report/verdict.py` is **the only place in the project that constructs a `Verdict`** — rule R4 — and a test
asserts that by reading every source file. The policy, in full:

    UNSUPPORTED   something outside the supported subset was involved, so no honest answer is possible
                  (an indirect jump, a jump table, a block that falls off the end of a function). [C3]
    MISMATCH      at least one finding of severity `error`.
    MATCH         no error, AND the matcher returned an isomorphism with every block mapped.

The last line is the important one: **MATCH requires a positive result, not an absence of complaints.** A
bug that silently produced no findings would give MISMATCH, not a false MATCH.

Severity, not kind, decides. `WRONG_BRANCH_CONDITION` is a `warning` by default, because what "consistent
control-flow semantics" means is **[C1]**: readings (a)–(c) are about structure, and compilers legitimately
rewrite a test. `--require-conditions` raises those findings to errors, which is reading (d). `--strict`
turns off UNSUPPORTED. Both are parameters, never silent defaults.

## 5. The checkpoint

> **Checkpoint: the same matcher works across different supported input programs without program-specific
> rules.**

`bash scripts/checkpoint_part7.sh` shows this two ways.

**By construction.** The checkpoint greps the matcher's own source for every function name, program name and
ISA name in the project and fails if it finds one. `matcher.py`, `invariants.py` and `diagnose.py` contain no
such string, in code or in a comment.

**By running it.** 15 C programs (23 functions) × 3 candidate architectures = 69 comparisons:

| | Result |
|---|---|
| correct assembly, x86-64 / AArch64 / RISC-V | **67 MATCH**, 3 MISMATCH |
| the 3 mismatches | all `count_primes`, all three ISAs — Part 6 finding F2, a real shape difference |
| random generated programs | MATCH |

`count_primes` is not a false alarm. Clang `-O0` compiles `do { } while (1)` into a real conditional branch
on a register holding 1, so the assembly has a decision the IR folded away, and the graphs genuinely differ.
The matcher reports it, with the extra block named; hiding it would be the bug. A constant-propagation rule
N9 would close it — see **[C5]**.

### Catching what is broken

A matcher that answered MATCH for everything would pass the table above perfectly. So the assembly is
deliberately damaged, and each mutation must produce a *specific* finding:

| Mutation | Verdict | Finding | What it says |
|---|---|---|---|
| a conditional branch sent to the other arm | MISMATCH | `WRONG_BRANCH` | "the arms of the decision in B0 lead elsewhere" |
| a `jmp` deleted (target ≠ the next block) | MISMATCH | `MISSING_EDGE` | "the U edge B2 → B7 has no counterpart" |
| a `ret` deleted | UNSUPPORTED | `UNSUPPORTED_CONSTRUCT` | "contains `falls_off_end`" |
| an inner loop's back edge redirected | MISMATCH | `MISSING_LOOP_BACK_EDGE` | "the loop back edge B4 → B3 has no counterpart" |
| `jle` changed to `jne` | MISMATCH | `WRONG_BRANCH_CONDITION` | "tests `ge.s(var, var)`… the assembly tests `eq(var, var)`" |
| a function renamed | MISMATCH | `FUNCTION_MISSING` | "`sum_to` is defined in the C program but not in the assembly" |

And, just as important, the opposite direction: **deleting a `jmp` whose target is the very next block must
still be MATCH.** Control falls through to the same place, so the program is unchanged. A matcher that cries
wolf is as useless as one that never does.

All ten of the brief's finding kinds are produced from real inputs in
`test_every_finding_kind_the_brief_asks_for_can_be_produced` — no hand-built CFGs, no fabricated findings.

## 6. Findings

* **F1 — arm order is not structure.** Requiring `T` to map to `T` failed 51 of 69 correct pairs. The
  polarity-free key from rule N6 is not a nicety; without it, nothing on AArch64 or RISC-V matches.
* **F2 — the condition is not structure either.** Folding the condition key into the isomorphism test failed
  a further 13 correct pairs, all of them compiler rewrites of the test (`x > 1` → `x >= 2`, `sltiu` for a
  signed compare, a sign-bit test for a comparison with zero).
* **F3 — equal block content is the wrong test.** Every correct program fails it, because the assembly has a
  prologue the IR does not and computes comparisons with arithmetic. Containment, used as a tie-breaker, is
  the honest version.
* **F4 — a bad mutation proves nothing.** Two of the first mutations written here were semantic no-ops:
  deleting a jump to the immediately following block, and changing `jg` to `jge` (which Part 6 finding F4
  says is invisible to the condition key by design). The matcher answered MATCH, and it was right both
  times. A mutation test is only evidence if the mutation actually changes the program.
* **F5 — return paths are edges into EXIT, not `ret` instructions.** Clang `-O0` funnels every `return`
  statement into a single `ret`, so counting `ret` instructions finds nothing. After rule N4, the number of
  ways out of a function is the number of edges into the virtual EXIT node.

## 7. What is still open

* **[C1]** — which level of consistency is required. The matcher implements (a)–(c) by default and (d) with
  `--require-conditions`; the professor's answer decides which is the real requirement.
* **[C3]** — whether UNSUPPORTED may be answered at all, or whether an unsupported construct must be a
  MISMATCH. `--strict` implements the second reading.
* **[B4]** — what an extra or missing function means. Today both are errors.
* **[A5]** — platform name decoration. Only an exact symbol-name match pairs two functions; a leading `_` on
  Mach-O or Windows is not assumed, and anything unpaired is reported rather than guessed.
