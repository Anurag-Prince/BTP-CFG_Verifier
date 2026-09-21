# Part 6: CFG normalization

The two CFGs the project builds describe the same program, but they do not look alike. Comparing them as they
are would mean comparing C text with assembly text — which the brief forbids and which would be meaningless
anyway, because `%cmp`, `%eax` and `w8` are names, not behaviour.

Normalization is the stage that makes them comparable. It has two halves:

* **A common vocabulary.** Every instruction becomes one of a small set of *semantic operations* —
  `ASSIGN`, `ADD`, `COMPARE`, `CONDITIONAL_BRANCH`, … — with registers, SSA names, types and labels dropped.
* **A common shape.** Eight rewriting rules (N1–N8) remove the graph differences that are artefacts of the
  producer rather than of the program: jump-only blocks, split blocks, several return sites, branches on a
  constant.

Both halves run on **both CFGs with the same code**. Nothing in `src/cfgverify/normalize/rules.py` reads
`cfg.origin`, the ISA, or a file name, and a test asserts that by reading the source. If normalization
behaved differently for the two sides, every later comparison would be worthless.

```
  HLL CFG (Part 4)  ─┐                                  ┌─► normalized HLL CFG ─┐
                     ├─► normalize/  semantics ─ conditions ─ loops ─ rules N1-N8  ├─► Part 7 matcher
  ASM CFG (Part 5)  ─┘         (one implementation)     └─► normalized ASM CFG ─┘
                                        │
                                        └─► normalize/checks.py: P1-P6, the proof nothing was lost
```

Quick use:

```bash
cfgverify hll-cfg regression/c/while.c -o build/raw --no-svg
cfgverify asm-cfg build/while.aarch64.s --isa aarch64 -o build/raw --no-svg
cfgverify normalize build/raw/*.cfg.json -o build/norm --check -v   # -v prints every rule step
bash scripts/checkpoint_part6.sh                                     # the whole checkpoint
```

---

## 1. The semantic vocabulary

`normalize/semantics.py`. Every instruction gets exactly one category — the mapping is **total**, so nothing
is ever silently dropped.

The categories the project brief names are mandatory:

| | | | |
|---|---|---|---|
| `ASSIGN` | `ADD` | `SUBTRACT` | `MULTIPLY` |
| `DIVIDE` | `INCREMENT` | `DECREMENT` | `COMPARE` |
| `CONDITIONAL_BRANCH` | `UNCONDITIONAL_BRANCH` | `CALL` | `RETURN` |
| `LOAD` | `STORE` | | |

Real programs also contain `%`, `&`, `<<`, casts, `&x`, `switch`, `alloca` and SSA `phi` nodes. Each needs a
category of its own, or normalization would have to throw instructions away. These are **extensions** and are
**TO CONFIRM WITH PROFESSOR [C5]**; none of them changes the meaning of a category above:

`MODULO`, `NEGATE`, `BITWISE`, `SHIFT`, `CONVERT`, `ADDRESS`, `ALLOCATE`, `SELECT`, `PHI`,
`MULTIWAY_BRANCH`, `INDIRECT_BRANCH`, `UNREACHABLE`, `OTHER`

`MODULO` is deliberately not `DIVIDE`: `a / b` and `a % b` compute different things, and `gcd` and `is_prime`
in the regression set depend on the difference. `OTHER` exists so that an instruction no rule recognises is
*counted and reported* rather than ignored.

### The mapping

There are three levels, and Part 6 adds the third:

| Level | `i++` in the IR | `i++` on x86-64 | `i++` on AArch64 |
|---|---|---|---|
| raw instruction | `%inc = add nsw i32 %5, 1` | `addl $1, -12(%rbp)` | `add w8, w8, #1` |
| normalized operation (Parts 4–5) | `v6 = ARITH.add v5, 1` | `ARITH.add $1, -12(%rbp)` | `ARITH.add w8, w8, #1` |
| **semantic operation (Part 6)** | **`INCREMENT`** | **`INCREMENT`** | **`INCREMENT`** |

The rules, in full:

| Normalized operation | Semantic category |
|---|---|
| `ALLOC` | `ALLOCATE` |
| `LOAD` / `STORE` | `LOAD` / `STORE` |
| `MOVE` | `ASSIGN` |
| `ARITH.add` | `ADD`, or **`INCREMENT`** when one operand is a literal one |
| `ARITH.sub` | `SUBTRACT`, or **`DECREMENT`** when one operand is a literal one |
| `ARITH.mul` / `.div*` / `.rem*` | `MULTIPLY` / `DIVIDE` / `MODULO` |
| `ARITH.and/or/xor/not` → `BITWISE`, `.shl/.shr*` → `SHIFT`, `.neg` → `NEGATE` | |
| `CMP` | `COMPARE` |
| `CAST` / `ADDR` / `CALL` / `PHI` / `SELECT` | `CONVERT` / `ADDRESS` / `CALL` / `PHI` / `SELECT` |
| `BRANCH` / `JUMP` / `SWITCH` | `CONDITIONAL_BRANCH` / `UNCONDITIONAL_BRANCH` / `MULTIWAY_BRANCH` |
| `RETURN` / `UNREACHABLE` / `INDIRECT` | `RETURN` / `UNREACHABLE` / `INDIRECT_BRANCH` |
| anything else | `OTHER` |

"A literal one" is recognised however the producer spells it: LLVM `1`, AT&T `$1`, Intel `1`, AArch64 `#1`,
and hex or binary forms.

### Block content

A block's comparable content is the ordered list of its semantic categories, with two deliberate omissions:

* **control-only operations** (`JUMP`, `BRANCH`, `SWITCH`, `INDIRECT_BRANCH`) — the graph already carries
  that information, so counting it twice would weight branchy code twice;
* **`ALLOCATE` and `PHI`** — SSA IR has `alloca`/`phi` and register assembly does not, by construction.

Both are counted and available; they are excluded only from *content* comparison, and `content_profile()`
gives the order-insensitive form for the cases where the two sides schedule instructions differently.

## 2. Branch conditions in a comparable form

`normalize/conditions.py`. The same test, written five ways:

| | |
|---|---|
| C | `while (i <= n)` |
| LLVM IR | `%cmp = icmp sle i32 %0, %1` then `br i1 %cmp, label %body, label %end` |
| x86-64 AT&T | `cmpl -4(%rbp), %eax` then `jg .LBB0_3` |
| AArch64 | `subs w8, w8, w9` then `b.gt .LBB0_3` |
| RISC-V | `blt a1, a0, .LBB0_3` |

All five reduce to one key: **`ge.s(var, var)`**. Four things are normalized:

1. **The relation.** LLVM spells it `sle`/`ugt`/`oeq`; the assembly plug-ins spell it `le.s`/`gt.u`/`eq`.
   Both become `(relation, signedness)`.
2. **Operand order and shape.** Operands become shapes — `var`, `zero`, `const:<value>` — never names. When
   the shapes differ, they are sorted and the relation mirrored to match (`a < 5` and `5 > a` are one test).
   AT&T's `cmp src, dst` order, and destination-first flag setters such as `subs w8, w8, w9`, are undone
   first.
3. **Polarity (rule N6).** A compiler may emit the opposite test and swap the arms. Each edge carries its own
   key *and* `polarity_free_key()`, which is a canonical representative of everything that cannot be told
   apart: `{relation, its negation}`, plus their mirrors when both operands have the same shape.
4. **Sign-bit tests.** AArch64 tests a value's sign with `tbnz w8, #31` rather than comparing it with zero.
   That is recognised and becomes `lt.s(var, zero)`, the same key the IR's `icmp slt %x, 0` produces.

**What is deliberately not done:** deciding that two `var` operands are the *same* variable. That needs a
value mapping between SSA names and a register file — matcher level **L4**, which depends on **[C1]**. A
consequence worth stating plainly: with two `var` operands and unknown arm order, `i <= n` and `n < i` are
indistinguishable, so `lt`, `le`, `gt` and `ge` collapse into one key. That is sound but coarse; it is
exactly the precision L4 will add.

## 3. Loop structure in a comparable form

`normalize/loops.py`. The analysis from Part 4 already finds back edges, natural loops and nesting depth. What
Part 6 adds is a description with **no block names in it**, so the two sides can be put side by side:

```
depth       how deeply this loop is nested
exits       how many blocks inside it can leave it       (1 = a plain loop, 2+ = a `break`)
back_edges  how many back edges reach its header         (2+ = a `continue` in a `while`, observation O5)
children    the loops directly inside it, same form
```

`count_pairs` (a `for` inside a `for`) gives `{L(d1,x1,b1[L(d2,x1,b1[])])}`; `sum_to` gives `{L(d1,x1,b1[])}`.

## 4. The rules

`normalize/rules.py`. Each rule removes one class of difference that is an artefact of the producer.

| Rule | Rewrite | Why it is needed |
|---|---|---|
| **N1** unreachable removal | delete blocks the entry cannot reach | dead blocks left by either side |
| **N2** jump-only bypass | a block that only transfers control is spliced out, its predecessors retargeted | Clang `-O0` `if.end` pads; the extra block the leader algorithm makes when an LLVM machine block has two terminators (Part 5) |
| **N3** chain merge | `A → B` where A has one successor and B one predecessor become one block | a label in the middle of straight-line code splits a block on the assembly side only |
| **N4** single virtual EXIT | every block with no successor gets an edge to one `EXIT` node | Clang `-O0` funnels every `return` into one `ret`; assembly may have several epilogues |
| **N5** degenerate branch | a two-way branch whose arms are the same block becomes a jump | equivalent encodings of a branch that decides nothing |
| **N6** polarity-free conditions | annotate every conditional edge with its comparable key and its polarity-free key (no rewrite) | compilers invert a test and swap the arms freely |
| **N7** noreturn sink | blocks ending `unreachable` get an edge to one `TRAP` node | `exit()`/`abort()` handling (Phase B, **[B3]**) |
| **N8** constant-condition fold | a branch on a literal keeps only the arm that can be taken | `do { } while (1)` is `br i1 true` in Clang `-O0` IR and a plain `jmp` in GCC's assembly (Part 3 observation P1) |

They run to a **fixpoint** in an order where each feeds the next — N8 and N5 create jumps, N1 clears dead
blocks, N2 removes the jumps, N3 merges what is left — and then the virtual nodes go on and N6 keys the
decisions that survived. Any rule can be switched off (`--rules N1,N2,N3`), so the effect of each one is
measurable rather than asserted.

Every application is logged as a `NormalizationStep` (rule, blocks, the raw block labels behind them, and a
sentence), the log is written next to the CFG as `*.rules.json`, and every normalized block records its
`origin_blocks`. A reviewer can replay the whole run, and Part 8's explanation layer can quote it.

### A worked example

`sum_to` from `regression/c/while.c`, normalized from both sides:

```
  from LLVM IR                                    from AArch64 assembly
  B0   entry       STORE STORE STORE              B0   sum_to    SUBTRACT STORE STORE ASSIGN STORE
  B1   while.cond  LOAD LOAD COMPARE              B1   .LBB0_1   LOAD LOAD SUBTRACT
  B2   while.body  LOAD LOAD ADD STORE            B2   .LBB0_2   LOAD LOAD ADD STORE
                   LOAD INCREMENT STORE                          LOAD INCREMENT STORE
  B3   while.end   LOAD RETURN                    B3   .LBB0_3   LOAD ADD RETURN
  EXIT             RETURN                         EXIT           RETURN

  B0 -> B1  U                                     B0 -> B1  U
  B1 -> B2  T  ge.s(var, var)                     B1 -> B3  T  ge.s(var, var)
  B1 -> B3  F  ge.s(var, var)                     B1 -> B2  F  ge.s(var, var)
  B2 -> B1  U  back edge                          B2 -> B1  U  back edge
  B3 -> EXIT U                                    B3 -> EXIT U
```

Five blocks, five edges, one decision, one loop and the same condition key on both sides. The loop body is
identical operation for operation. What is left is exactly the real difference: the assembly prologue
(`SUBTRACT` on the stack pointer, an extra `ASSIGN`), the `SUBTRACT` that AArch64 uses to compare, and the
`T`/`F` arms sitting the other way round — which is why N6 exists, and why `ge.s(var, var)` is the same on
both sides despite `sle` on one and `b.gt` on the other.

## 5. The checkpoint: nothing is lost

> **Checkpoint: raw CFGs become normalized CFGs without losing control-flow meaning.**

The rules could be wrong, so nothing here trusts them. `normalize/checks.py` computes six properties from the
raw CFG and from the normalized CFG independently and requires them to agree — the same discipline as LLVM's
`dot-cfg` in Part 4 and the compiler's block marks in Part 5.

| | Property | How it is checked |
|---|---|---|
| **P1** | **What the program computes, on every path** | Every path of the *acyclic view* (each back edge redirected to one synthetic "round the loop again" sink) is reduced to its sequence of semantic operations. Normalization may never produce a behaviour the raw CFG did not have; without a logged N5/N8 fold the two sets must be **equal**. |
| **P2** | **Which code can repeat** | Every raw block that was inside a loop must still be inside one, and every block that was not must still not be. Nesting may not get deeper; loops may not all vanish. |
| **P3** | **Decisions** | The decision count drops by exactly the number of logged N5/N8 folds, and with no fold the polarity-free condition keys are unchanged. |
| **P4** | **Reachability** | For every pair of surviving raw blocks, "u can reach v" has the same answer. |
| **P5** | **Dominance** | Likewise for "u dominates v" (computed by networkx, not by our own code). |
| **P6** | **Provenance** | Every normalized block's `origin_blocks` are real raw blocks, no raw block is claimed twice, and every raw block that disappeared has a rule step that accounts for it. |

P1 is the strong one: it is a statement about executions, not about graph shape. The checks are themselves
tested by **mutation** — dropping a branch arm, deleting a loop's back edge, swapping two blocks' contents,
and inventing a provenance entry each have a test asserting the checks catch them.

### Result

`bash scripts/checkpoint_part6.sh`:

| Input | Result |
|---|---|
| 23 HLL CFGs (15 C programs, 23 functions) | normalized, P1–P6 all pass |
| 69 assembly CFGs (the same functions × x86-64, AArch64, RISC-V) | normalized, P1–P6 all pass |
| random programs (`tests/random_c.py`) | normalized, P1–P6 all pass |
| the same CFG labelled `hll` and labelled `asm` | produces the identical graph |
| the rules run twice | second run changes nothing (fixpoint) |

### Does it actually help?

Not a matching test — that is Part 7 — but the measurement the whole part exists for. Same function, HLL CFG
versus assembly CFG, agreeing on (blocks, edges):

| ISA | raw | after normalization |
|---|---|---|
| x86-64 | 18 / 23 | **22 / 23** |
| AArch64 | 2 / 23 | **22 / 23** |
| RISC-V | 2 / 23 | **22 / 23** |

The AArch64 and RISC-V rows are the point: raw, those CFGs almost never lined up, because their branches
carry two terminators and the leader algorithm splits there. Comparable branch conditions agree in **53 of 69**
function/ISA pairs.

## 6. Findings

Things learned here that shape the later parts:

* **F1 — N2 can correctly reduce the loop count.** When the header of an outer loop is an empty block,
  splicing it out gives the outer and the inner loop the same header, and two nested loops become one. The
  control flow is unchanged, so a preservation check written as "the same number of loops" is *wrong*; the
  property that must hold is "which code can repeat" (P2).
* **F2 — a constant in a register defeats N8.** Clang `-O0` compiles `do { } while (1)` into a real
  conditional branch on a register holding 1, so on the assembly side N8 cannot see that the condition is
  constant while on the IR side it can. This is the only function in the regression set (`count_primes`)
  whose two normalized CFGs still differ in shape. Folding it needs a small constant-propagation step —
  proposed as a possible **N9**, and part of **[C5]**.
* **F3 — compilers rewrite conditions.** `x > 1` may be emitted as `x >= 2`, an unsigned `sltiu` may be used
  for a signed test, and a sign test may replace a comparison. These are legitimate and account for all 16
  condition-key disagreements. They are a matcher concern (L4 / **[C1]**), not a normalization bug, and
  none of them changes the graph.
* **F4 — the polarity-free key is coarse by necessity.** With two `var` operands and unknown arm order,
  `lt`, `le`, `gt` and `ge` collapse into one key. Recovering the difference — and with it the ability to
  detect swapped `if` arms, the weakness recorded in ARCHITECTURE §10 — needs the value mapping of L4.

## 7. What is still open

The rule set is a **proposal**, **TO CONFIRM WITH PROFESSOR [C5]**, and so are the extension categories in
§1. Specifically worth asking:

* Are N1–N8 the right set, and should N9 (constant propagation, finding F2) be added?
* Are the extension categories acceptable, or should the vocabulary stay exactly as the brief lists it —
  and if so, what should happen to a `%` or a `switch`?
* Is the coarse polarity-free key acceptable for the first milestone, with branch polarity deferred to L4
  (**[C1]**)?
