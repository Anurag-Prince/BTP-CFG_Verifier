# Part 3: LLVM IR generation and parsing

This part covers four things:

- how to **generate** LLVM IR from the supported C programs;
- what the parts of IR mean (functions, labels, basic blocks, instructions, terminators, branches, comparisons,
  return);
- **how IR represents each supported control-flow construct**;
- the **IR reader** (`src/cfgverify/hll/ir_reader.py`), which extracts blocks, instructions, terminators, branch
  targets, predecessors and successors.

**Checkpoint (§12).** New C files, written *after* the reader was frozen, were compiled to IR, and their blocks and
edges were extracted without changing the reader. Every result agrees with three independent views from LLVM itself.

All IR shown here is real output of Ubuntu Clang 18.1.3 with the project flags
`-S -emit-llvm -O0 -fno-discard-value-names` unless stated otherwise.

---

## 1. Generating IR

```bash
cfgverify emit-ir regression/c/if.c -o build/ir/if.ll            # C -> .ll with the canonical flags
cfgverify parse-ir regression/c/if.c                              # compile, then list blocks and edges
cfgverify parse-ir regression/c/if.c -g --instructions            # ... with every instruction and its C line
cfgverify parse-ir regression/c/if.c --json build/if.json --crosscheck
```

| Flag | Why |
|---|---|
| `-S -emit-llvm` | Write textual IR (`.ll`), which is what the reader reads |
| `-O0` | No optimization. The IR keeps the source's `if`/loop structure (see Part 2) |
| `-fno-discard-value-names` | Readable names (`if.then`, `%cmp`). Without it Clang numbers everything (`7:`, `%6`). The reader handles both (§4) |
| `-g` (optional) | Adds `!dbg` source locations. The reader turns them into C line numbers per instruction and per block |
| `--target=<triple>` (optional) | Only when the target is confirmed ([A1], [A7]). Part 2 showed the `-O0` CFG of our programs is the same on six triples |

**What `-O0` IR looks like.**
- Every local variable gets a stack slot (`alloca`), and every use is a `load`/`store`. There are no `phi` nodes for
  the supported constructs.
- Every function has the `optnone` attribute, so later LLVM passes cannot change it.
- These properties are why the `-O0` CFG follows the C source so closely.

## 2. Anatomy of a `.ll` file

```llvm
; ModuleID = 'regression/c/if.c'
source_filename = "regression/c/if.c"                               ; (1) module header
target datalayout = "e-m:e-p270:32:32-...-S128"                     ;     type sizes/alignments of the target
target triple = "x86_64-pc-linux-gnu"                               ;     target the IR was generated for

; Function Attrs: noinline nounwind optnone uwtable
define dso_local i32 @clamp_negative(i32 noundef %x) #0 {          ; (2) a function definition
entry:                                                              ; (3) a label: start of basic block "entry"
  %x.addr = alloca i32, align 4                                     ; (4) instructions
  %y = alloca i32, align 4
  store i32 %x, ptr %x.addr, align 4
  %0 = load i32, ptr %x.addr, align 4
  store i32 %0, ptr %y, align 4
  %1 = load i32, ptr %x.addr, align 4
  %cmp = icmp slt i32 %1, 0                                         ; (5) a comparison -> i1
  br i1 %cmp, label %if.then, label %if.end                         ; (6) terminator: conditional branch

if.then:                                          ; preds = %entry  ; (7) LLVM's comment: who jumps here
  store i32 0, ptr %y, align 4
  br label %if.end                                                  ;     terminator: unconditional branch

if.end:                                           ; preds = %if.then, %entry
  %2 = load i32, ptr %y, align 4
  ret i32 %2                                                        ;     terminator: return
}

attributes #0 = { noinline nounwind optnone uwtable ... }            ; (8) attribute groups
!llvm.ident = !{!5}                                                 ; (9) metadata
!5 = !{!"Ubuntu clang version 18.1.3 (1ubuntu1)"}
```

## 3. Functions

- `define` = a function **with a body**. This means one CFG.
- `declare` = an external function with no body, such as `printf` or `llvm.umax.i32`. It has no CFG, and the reader
  only lists its name.
- The signature is: linkage/visibility (`dso_local`), return type (`i32`, `void`), `@name`, parameters with
  attributes (`i32 noundef %x`), an attribute group (`#0`), and, with `-g`, `!dbg !N`, which points to the
  function's source line.
- Names starting with `@` are global (functions, globals). Names starting with `%` are local to the function
  (values, blocks).

## 4. Labels and basic blocks

- A **label** at the start of a line (`if.then:`) starts a new basic block. The block runs until its terminator.
- **Named vs numbered.** Without `-fno-discard-value-names`, Clang prints the same function as below. The structure
  is identical; only the names change. The reader extracts the same CFG from both (tested on every regression
  program).

  ```llvm
  define dso_local i32 @clamp_negative(i32 noundef %0) #0 {
    %2 = alloca i32, align 4               ; no "entry:" label. The entry block is implicitly %1,
    ...                                    ; the next number after the argument %0
    %6 = icmp slt i32 %5, 0
    br i1 %6, label %7, label %8
  7:                                                ; preds = %1
    store i32 0, ptr %3, align 4
    br label %8
  8:                                                ; preds = %7, %1
  ```
- **The entry block** is the first block. LLVM forbids branches to it, so it never has predecessors. If it is
  printed without a label, it takes the first free number after the unnamed arguments. The reader computes that
  number, so its names match LLVM's (`%1` above).
- **`; preds = ...`** is a *comment* written by LLVM's printer. Blocks without predecessors say
  `; No predecessors!`. The reader **computes** predecessors from the terminators and only uses these comments to
  cross-check itself (§11).
- The **order of blocks in the file is not execution order**. Only edges define control flow.
- Other accepted spellings: quoted names (`"if end":`) and the old LLVM ≤ 8 style `; <label>:12:`.

## 5. Instructions

Every instruction is one line, with two exceptions that span several lines: `switch`, and the `to label` part of
`invoke`/`callbr`. An instruction that produces a value names it: `%cmp = icmp ...`. This is SSA form: each `%value`
is assigned exactly once.

| Opcode (seen in our programs) | Meaning |
|---|---|
| `alloca` | reserve a stack slot (one per C variable at `-O0`) |
| `load` / `store` | read or write memory (a variable's slot, or an array element) |
| `add nsw`, `sub`, `mul`, `sdiv`, `srem` | integer arithmetic (`nsw` = no signed wrap) |
| `getelementptr`, `sext` | address of `arr[i]` and widening `int` → 64-bit index (array programs) |
| `icmp` | integer comparison → `i1` |
| `call` | function call. **Not** a terminator (execution continues after it) |
| `phi`, `select`, `zext` | appear with `&&`/`||`/`?:` used as values, and in optimized IR |
| `br`, `switch`, `ret`, `unreachable` | terminators (§6) |

**Trailing metadata** such as `, !dbg !23` (source location) or `, !llvm.loop !6` (Clang marks each loop latch) is not
an operand. The reader strips it before reading operands.

**Debug bookkeeping.** With `-g`, Clang 18 emits `call void @llvm.dbg.declare(...)`. LLVM 19 and later print these as
`#dbg_declare(...)` *records*, which are not instructions. The reader skips records, and it ignores `llvm.dbg.*` calls
when computing a block's source lines. The CFG is the same either way: tested by re-printing all regression IR with
LLVM 22.

## 6. Terminators

Every block ends with **exactly one** terminator, and there are no terminators anywhere else in the block. The
terminator is the only place control flow is written down.

| Terminator | Example | Successors (roles as the reader reports them) | Phase A |
|---|---|---|---|
| unconditional `br` | `br label %for.cond` | 1: `U` | yes |
| conditional `br` | `br i1 %cmp, label %if.then, label %if.end` | 2: `T`, `F` | yes |
| `ret` | `ret i32 %2`, `ret void` | 0: the function returns | yes |
| `unreachable` | `unreachable` | 0: control never gets here (for example after `exit()`) | yes |
| `switch` | multi-line (below) | `default`, then `case:<value>` for each case | no ([B3]) |
| `indirectbr` | `indirectbr ptr %a, [label %x, ...]` | `indirect` × n (GNU computed goto) | no |
| `invoke` / `callbr` | `... to label %ok unwind label %lp` | `normal`/`unwind`; `default`/`indirect` | no |
| `resume`, `catchswitch`, `catchret`, `cleanupret` | exception handling (C++), not produced by C | — | no |

```llvm
  switch i32 %21, label %24 [          ; default target first ...
    i32 1, label %22                   ; ... then one line per case
    i32 2, label %23
    i32 3, label %23                   ; two cases -> two edges to the same block
  ]
```

The reader reads every terminator correctly. Anything outside Phase A is flagged (`Function.unsupported_terminators()`),
so a later stage can report `UNSUPPORTED` instead of guessing.

## 7. Conditional and unconditional branches

- `br label %X` always goes to `X`.
- `br i1 %c, label %T, label %F` goes to `T` when the 1-bit value `%c` is 1, otherwise to `F`. In the text, the
  **true target is always written first**. The reader numbers successors in exactly LLVM's order: successor 0 = T,
  successor 1 = F. `opt -passes=dot-cfg` numbers them the same way, which is what the cross-check compares.
- **Pitfall (found in Part 0):** LLVM's *in-memory* operand list, which llvmlite exposes, stores the targets as
  [false, true]. That is why the reader works from the text order.
- At `-O0`, the condition is computed **right before** the branch, in the same block
  (`%cmp = icmp ...` then `br i1 %cmp`). `Function.condition_of(block)` returns that defining instruction. On
  random programs, the tests check that the compare is always the instruction immediately before the branch.
- **The condition can be a constant.** `do { ... } while (1)` becomes `br i1 true, label %do.body, label %do.end`:
  a 2-way block whose F edge can never be taken. Compare this with `while (1)`, where Clang writes no condition block
  at all (Part 2, O5). See §13.

## 8. Comparisons

`icmp <predicate> <type> <a>, <b>` gives an `i1`:

| C (`int`) | `==` | `!=` | `<` | `<=` | `>` | `>=` |
|---|---|---|---|---|---|---|
| signed | `eq` | `ne` | `slt` | `sle` | `sgt` | `sge` |
| unsigned | `eq` | `ne` | `ult` | `ule` | `ugt` | `uge` |

- `if (x)` on an `int` becomes `icmp ne i32 %x, 0`.
- A `_Bool` is loaded as `i8` and converted with `trunc ... to i1`.
- Floating point uses `fcmp` with *ordered* (`olt`) and *unordered* (`ult`) predicates. This is outside Phase A.
- In a **condition**, `a && b` becomes two blocks, each with its own `icmp` and branch (short-circuit). As a
  **value** (`t = a && b;`) it also needs a `phi`. The reader handles both, and the random "extended" programs
  test them.

## 9. Return

- `ret <type> <value>` or `ret void` ends the function.
- If a C function has **several `return` statements**, Clang `-O0` stores the value into `%retval` and jumps to one
  shared `return` block, so there is still **exactly one `ret`**. The random-program tests check this on every
  function.
- A function with a single `return` inside a loop returns directly from that block.

## 10. How IR represents each supported construct

Diagrams of every case are in [`CONTROL_FLOW_FUNDAMENTALS.md`](CONTROL_FLOW_FUNDAMENTALS.md).

| C construct | Blocks Clang creates | Terminators | Back edge |
|---|---|---|---|
| sequence | none. Statements (calls too) stay in the current block | — | — |
| `if (c) S` | `if.then`, `if.end` | cond: `br i1 %c, %if.then, %if.end`; then: `br %if.end` | — |
| `if (c) S1 else S2` | `if.then`, `if.else`, `if.end` | cond → then/else; both `br %if.end` | — |
| `else if` | a nested `if-else` inside `if.else`, with its own `if.end` (jump-only) | as above, nested | — |
| `while (c) S` | `while.cond`, `while.body`, `while.end` | pre: `br %while.cond`; cond: `br i1 → body / end`; body: `br %while.cond` | `while.body → while.cond` |
| `for (i; c; s) S` | `for.cond`, `for.body`, `for.inc`, `for.end` | init in pre-block; cond: `br i1 → body / end`; body → inc; inc: `br %for.cond` | `for.inc → for.cond` |
| `do S while (c)` | `do.body`, `do.cond`, `do.end` | pre: `br %do.body`; cond: `br i1 → body / end` | `do.cond → do.body` |
| `break` | none. `br` to the loop's `*.end` | `br label %for.end` | — |
| `continue` | none. `br` to `for.inc` / `while.cond` / `do.cond` | `br label %for.inc` | in a `while` loop it is a second back edge |
| `return e` | none. A store to `%retval` and `br label %return` (or `ret` directly if it is the only return) | `ret` in the `return` block | — |
| `while (1)` | no `while.cond` block | pre: `br %while.body` | from the body's latch |
| `do ... while (1)` | `do.cond` with `br i1 true` | constant conditional branch | `do.cond → do.body` |

`while` and `do-while` in real IR:

```llvm
; while (i <= n) { sum += i; i++; }            ; do { digits++; n /= 10; } while (n != 0);
  br label %while.cond                         ;   br label %do.body
while.cond:                                    ; do.body:        ; preds = %do.cond, %entry
  %cmp = icmp sle i32 %0, %1                   ;   ...
  br i1 %cmp, label %while.body, label %while.end;   br label %do.cond
while.body:                                    ; do.cond:        ; preds = %do.body
  ...                                          ;   %cmp = icmp ne i32 %2, 0
  br label %while.cond, !llvm.loop !6          ;   br i1 %cmp, label %do.body, label %do.end, !llvm.loop !6
while.end:                                     ; do.end:
  ret i32 %5                                   ;   ret i32 %3
```

---

## 11. The IR reader

### 11.1 Design (a change from the Part 0 proposal)
Part 0 proposed llvmlite as the reader. It is now a **dedicated text reader**, with llvmlite demoted to one of
three oracles. The reasons:

- **One LLVM, not two.** llvmlite bundles its own LLVM (22.1), while Clang is 18.1. An older bundle cannot read IR
  from a newer Clang.
- **Evidence.** Later explanations need exact `.ll` line numbers, names exactly as printed, and `!dbg` source lines.
  llvmlite exposes none of these conveniently.
- **Transparency.** Control flow in IR is completely described by labels and terminators, so a small, fully tested
  reader is enough and easy to audit.

### 11.2 Data model (`ir_reader.py`)

```
Module      source_filename, target_triple, datalayout, functions{name: Function}, declarations[]
Function    name, signature, params, line, source_line, blocks[] (file order), entry,
            edges(), condition_of(block), defining_instruction(value), unsupported_terminators(), to_networkx()
BasicBlock  name, label_line, instructions[], terminator, successors, predecessors, source_lines,
            annotated_preds (LLVM's "; preds" comment, used only for cross-checking)
Instruction text, opcode, result, line (.ll), dbg, source_line (C), is_terminator, is_debug_intrinsic
Terminator  kind (br | cond_br | ret | unreachable | switch | ...), successors [Successor(target, role)], condition
Comparison  instruction, opcode (icmp | fcmp | trunc | ...), predicate, operands
```

### 11.3 How it reads
1. **Module level:** `source_filename`, `target triple`, `declare` names, `!N = !DILocation(line: L ...)`, and each
   function's `DISubprogram` line.
2. **Function header:** `define … @name(` with a balanced scan of the parameter list (parameters can contain
   parentheses), until `{`. The body ends at a `}` in column 0.
3. **Lines → instructions:**
   - A label in column 0 starts a block.
   - Comments are removed, except `;` characters inside strings.
   - An instruction continues onto the next line while brackets are open (`switch [ ... ]`) or when the next line
     starts with `to`/`unwind`/`]`.
   - `#dbg_` records are skipped.
4. **Entry block without a label:** it is named with the next free number after the unnamed arguments.
5. **Terminators:** each kind is parsed into ordered `(target, role)` pairs, with metadata attachments stripped first.
6. **Predecessors:** computed from all successors. They are unique per block; `predecessor_counts()` keeps one count
   per edge, because a switch can reach the same block twice.
7. **Checks:** every block must end in a terminator, with no terminator in the middle; every target must exist; block
   names must be unique. Malformed input raises `IRParseError` with the `.ll` line number.

### 11.4 Using it

```python
from cfgverify.hll.ir_reader import read_ir
m = read_ir("build/part3/collatz.ll")
fn = m.functions["collatz_steps"]
for b in fn.blocks:
    print(b.name, b.terminator.kind, [(s.role, s.target) for s in b.terminator.successors], b.predecessors)
fn.condition_of("while.cond")   # Comparison(opcode='icmp', predicate='ne', operands=['i32 %1', '1'], ...)
```

`cfgverify parse-ir FILE.c|FILE.ll [-g] [-i] [-f FUNCTION] [--json OUT] [--crosscheck]` prints one table per
function (see §12). `--json` writes everything (blocks, instructions with `.ll` and C lines, terminators, roles,
predecessors, edges) for the next parts.

---

## 12. Verification and the Part 3 checkpoint

**Three independent LLVM views** (`hll/ir_crosscheck.py`, `--crosscheck`) must agree with the reader:

| Oracle | What must match |
|---|---|
| `opt -passes=dot-cfg` | the same blocks; the same edges **with the same successor index** (T = 0, F = 1, switch default = 0, …) |
| LLVM's `; preds =` comments | every block's predecessors, counted per edge |
| llvmlite (LLVM's own parser) | the number of blocks, the block names, and the **opcode sequence of every block**, which catches mistakes in joining multi-line instructions |

**Tests** (`tests/test_ir_reader.py`, 134 with the default of 25 random programs per mode; 284 with the checkpoint's 100):
- **Hand-written IR:** named, numbered and quoted labels; an unlabeled entry; the old label style; multi-line
  `switch`/`invoke`/`callbr`; `indirectbr`; `resume`; dead blocks; `#dbg_` records; comments containing strings;
  four kinds of malformed input.
- **All 16 regression functions:** the reader reproduces the Part 2 expected CFG exactly (blocks, T/F/U edges, exits,
  predicates, back edges), and every listed C line falls in the right block.
- **Variants:** numbered names; `-O1`/`-O2` IR (with `phi`, `select`, `tail call`, …); RISC-V, AArch64 and MIPS
  triples; IR re-printed by LLVM 22.
- **Random C programs** (`tests/random_c.py`): structured programs with a median of about 34 conditions and 13 loops
  each.
  - *Supported* mode: besides the three oracles, the number of conditional branches must equal the number of
    conditions in the source, the number of natural loops must equal the number of loop statements, the compare
    must sit right before each branch, and there must be exactly one `ret`.
  - *Extended* mode adds `switch`, `&&`, `||`, `?:`, `goto` and dead code.
  - Each program is also read at `-O0` with numbered names and at `-O2`.
- **Mutation check:** two bugs were planted on purpose, and the tests caught both.
  - Swapping T/F in the reader made 56 of 90 tests fail (run with 3 random programs per mode).
  - Breaking multi-line joining made 6 of 94 tests fail (run with 5 per mode). These included the hand-written
    switch/invoke test and every random program that contains a `switch`.

**Checkpoint.** The reader and cross-checker were frozen first, and their checksums recorded:
`ir_reader.py` `bcd37295…`, `ir_crosscheck.py` `2ebc9f3f…`. Five new programs were then written in
`examples/c/part3/`:
- arrays and pointers;
- a `void` function;
- a call inside a condition;
- an early `return` inside a loop;
- `continue` in a `while` loop;
- `do … while (1)`.

`bash scripts/checkpoint_part3.sh` processed them unchanged:

| Function | Blocks | Edges | Cond. branches | Predicted by hand | Cross-check |
|---|---|---|---|---|---|
| `gcd` | 8 | 10 | 3 | 3 | ✔ |
| `lcm` | 6 | 7 | 2 | 2 | ✔ |
| `binary_search` | 11 | 13 | 3 | 3 | ✔ |
| `bubble_sort` | 13 | 16 | 4 | 4 | ✔ |
| `collatz_steps` | 9 | 11 | 3 | 3 (with 2 back edges, from `continue`) | ✔ |
| `is_prime` | 10 | 12 | 3 | 3 | ✔ |
| `count_primes` | 8 | 10 | 3 | **2**. The surprise was `br i1 true` from `do … while (1)` (§7) | ✔ |

The hand prediction for `count_primes` was wrong, and the reader was right. The IR really has a third, constant
conditional branch.

Run it on your own file with `bash scripts/checkpoint_part3.sh path/to/new.c`. It prints the reader's checksums, the
blocks and edges table and the JSON path, then runs the cross-check and the reader tests (100 random programs per
mode by default).

---

## 13. Observations for later parts

| # | Observation | Consequence |
|---|---|---|
| P1 | `do … while (1)` gives `br i1 true`: a 2-way block with a dead F edge. `while (1)` gives no condition block at all. | For Clang `-O0` assembly this is consistent. A probe on x86-64, AArch64 and RISC-V showed all three keep a real run-time test of the constant 1. Another producer ([A3]) may emit a plain jump, so normalization may need **N8: fold constant-condition branches** (proposal, [C5]). |
| P2 | Debug info adds instructions (Clang 18) or records (LLVM 19+) but never changes the CFG. | Evidence can use `-g` IR without affecting matching. |
| P3 | Named and numbered IR have the same structure. | Nothing may depend on names (see Part 2, O4). |
| P4 | Optimized IR (`-O1`/`-O2`) is read correctly, but it contains `phi`/`select` and restructured loops. | Reading it is fine. Verifying it is a separate question ([A4]). |
| P5 | `switch`, `indirectbr`, `invoke` and `callbr` are read, but flagged as outside Phase A. | A later stage reports UNSUPPORTED instead of guessing ([C3]). |
