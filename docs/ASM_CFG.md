# Part 5: Assembly CFG generator

> **The architecture to verify is still TO CONFIRM WITH PROFESSOR** ([A1], [A2] in `OPEN_QUESTIONS.md`).
> Nothing in this part assumes x86-64. The reader and the CFG builder contain no architecture knowledge at
> all; each architecture lives in its own plug-in, **no plug-in is the default**, and `--isa` must be given on
> every run (or `--isa auto`, which prints what it detected and repeats the reminder above). Three candidate
> plug-ins — x86-64 (AT&T and Intel), AArch64 and RISC-V — are implemented and tested so that whichever
> architecture the professor confirms, the work is already done and the others simply stop being exercised.

Input: an assembly file (`.s`). Output: one `model.cfg.CFG` per function, `origin: "asm"`, in exactly the same
`cfgverify.cfg/1` schema the HLL side produces in Part 4 — that identical shape is what makes Part 6 (CFG
normalization) and Part 7 (matching) possible.

```
        .s file ──► asm/reader.py ──► AsmFile{functions, instructions, labels}
                         │ (ISA-neutral: comments, directives, labels, mnemonic + operands)
                         ▼
                  asm/isa/<isa>.py   answers per instruction: class, targets, predicate, normalized operation
                         ▼
                  asm/cfg_builder.py ──► leaders ─► basic blocks ─► nodes + edges ─► CFG (origin "asm")
                         ▼
                  asm/cfg_checks.py  ──► schema, round trips, invariants, compiler block marks, assembler, targets
```

Quick use:

```bash
clang --target=aarch64-linux-gnu -S -O0 -g -o build/while.aarch64.s regression/c/while.c
cfgverify asm-cfg build/while.aarch64.s --isa aarch64 -o build/part5 --check
cfgverify asm-cfg regression/asm/*.s --isa auto -o build/part5 --check   # prints the detected ISA
bash scripts/checkpoint_part5.sh                                          # the whole checkpoint
```

---

## 1. Reading assembly without knowing the architecture

`asm/reader.py` does everything that is true of *every* GNU-as style assembly file and nothing else:

| Line                        | What the reader does                                                          |
|-----------------------------|-------------------------------------------------------------------------------|
| `# comment`, `// comment`   | dropped (the comment character comes from the plug-in: `#`, `//`, `;`)          |
| `.text`, `.cfi_*`, `.size`  | directive — not an instruction; `.type`/`.globl` are remembered as function symbols |
| `.loc 1 7 9`                | DWARF line marker — attaches a **C source line** to the instructions that follow |
| `.LBB0_1:` / `sum_to:`      | label — recorded with the index of the next instruction                        |
| `# %bb.2:`                  | a *machine block* annotation by Clang — remembered only as a cross-check oracle |
| `movl -12(%rbp), %eax`      | instruction — split into mnemonic + raw operand text, nothing interpreted       |

Functions are split on the `.type f, @function` / `.globl f` symbols the compiler emits. Hand-written files
have none, so the reader falls back to "a non-local label at the start of a line begins a function" — which is
why `regression/asm/sum_to.*.s` deliberately carry no directives at all: they prove the reader is not
secretly relying on a compiler.

Data records per file: `AsmFile{path, isa, functions, detection}`, `AsmFunction{name, instructions, labels,
source_file, marked_block_starts}`, `AsmInstruction{mnemonic, operands, text, line, labels, source_line}`.

## 2. The ISA plug-in contract

A plug-in (`asm/isa/base.py` → `x86.py`, `aarch64.py`, `riscv.py`) answers five questions about one
instruction, and nothing more:

1. **What class is it?** `COND_BRANCH | JUMP | INDIRECT_JUMP | CALL | RETURN | OTHER`
2. **Where can it go?** the label operands it may jump to.
3. **What does it test?** a normalized predicate: `lt.s`, `ge.u`, `eq`, `ne`, `eq0`, …
4. **What is the normalized operation?** the shared vocabulary of `model/ops.py`.
5. **How is the syntax written?** comment characters, local-label prefixes, operand order.

Most of a plug-in is therefore tables, e.g.

```python
_CC = {"e": "eq", "ne": "ne", "l": "lt.s", "le": "le.s", "g": "gt.s", "ge": "ge.s",
       "b": "lt.u", "be": "le.u", "a": "gt.u", "ae": "ge.u", ...}      # j<cc> and set<cc>/cmov<cc>

class X86(Isa):
    name, syntax, comment_chars = "x86-64", "att", ("#",)
    conditions = {f"j{cc}": pred for cc, pred in _CC.items()}          # jle -> le.s, jae -> ge.u, ...
    jumps, returns, calls = frozenset({"jmp", "jmpq"}), frozenset({"ret", "retq", ...}), frozenset({"call", ...})
    compares = frozenset({"cmp", "test", ...})
    arith = {"add": "add", "sub": "sub", "imul": "mul", "sar": "shr.s", ...}
```

AT&T writes the operand size as a mnemonic suffix (`movl`, `cmpq`, `addw`), so the x86 plug-in strips a
trailing `b/w/l/q` before looking a mnemonic up — one small override of `decode()`, and the tables stay short.

Two families of ISA are handled by the same code:

* **flag-based** (x86-64, AArch64): the comparison is a separate instruction (`cmpl %edi, %ecx`) and the
  branch only names a condition (`jg`). The builder scans **backwards from the branch** for the last
  flag-setting instruction in the block, and that is the condition.
* **compare-and-branch** (RISC-V): the branch carries its own operands (`bgt a2, a0, .Ldone`). The builder
  reads the operands straight off the branch.

Adding an architecture means adding one file of tables. It means changing **no** other file.

**Detection** (`--isa auto`) counts how many instruction lines use mnemonics each plug-in knows, and the
decision plus the full score table is always printed — a file is never silently assumed to be one ISA:

```
detected ISA for sum_to.riscv.s: riscv (riscv=8, x86-64=2, x86-64-intel=2, aarch64=2).
The verified architecture is still TO CONFIRM WITH PROFESSOR.
```

## 3. Basic blocks: the leader algorithm

Identical in shape to the HLL side, and ISA-neutral — the plug-in supplies only the classification:

```
leaders = {first instruction}
        ∪ {every instruction that a branch or jump can target}
        ∪ {the instruction after a conditional branch, jump or return}   (+ the ISA's delay slots)
a block runs from one leader up to (not including) the next
```

`delay_slots` is a plug-in field (0 for all three candidates; MIPS/SPARC would set 1), so a delayed-branch
architecture needs no change to the builder either.

**A call is not a terminator.** Control returns to the next instruction, so `call` never splits a block
([C2]). The callee name is kept on the node (`calls: ["is_prime"]`) for the matcher to use later.

## 4. Edges and branch information

| Last instruction of the block | Terminator    | Edges                                                        |
|-------------------------------|---------------|---------------------------------------------------------------|
| conditional branch            | `cond_br`     | `T` → the branch target, `F` → the fall-through block (in that order, always) |
| unconditional jump            | `br`          | `U` → the target                                              |
| jump/branch out of the function | `br_external` | none (a tail call; recorded, not guessed)                     |
| return                        | `ret`         | none — exit node                                              |
| indirect jump (jump table)    | `indirect`    | none, and reported in `analysis.unsupported` ([C4])           |
| anything else                 | `br`          | `U` → the next block (ordinary fall-through)                  |

Every `T`/`F` edge carries `BranchInfo`, the same field for field as the HLL side, so the two can be compared:

```json
{ "taken_when": "flags(cmpl %edi, %ecx) is true",
  "condition":  "flags(cmpl %edi, %ecx)",
  "compare":    "CMP.gt.s %edi, %ecx",
  "predicate":  "gt.s",
  "ir":         "cmpl\t%edi, %ecx",
  "source_line": 6, "source_text": "while (i <= n) {" }
```

`source_line`/`source_text` come from the `.loc` directives, so an assembly block can be pointed back at the C
line it came from — which is exactly what the Part 8 explanation layer will quote.

`compute_analysis()` is shared with the HLL side: back edges, natural loops, loop depth, cyclomatic
complexity, unreachable blocks. Nothing about it is architecture-specific.

## 5. A worked example

`regression/asm/sum_to.x86-64.s` (hand-written, no compiler directives):

```asm
sum_to:
	movl	$0, %eax
	movl	$1, %ecx
.Lcond:
	cmpl	%edi, %ecx
	jg	.Ldone
	addl	%ecx, %eax
	addl	$1, %ecx
	jmp	.Lcond
.Ldone:
	ret
```

produces:

```
B0  sum_to   br       MOVE $0, %eax ; MOVE $1, %ecx
B1  .Lcond   cond_br  CMP.cmp %edi, %ecx ; BRANCH.gt.s .Ldone
B2  @10      br       ARITH.add %ecx, %eax ; ARITH.add $1, %ecx ; JUMP .Lcond
B3  .Ldone   ret      RETURN

E0  B0 -> B1  U
E1  B1 -> B3  T   flags(cmpl %edi, %ecx) is true    CMP.gt.s %edi, %ecx
E2  B1 -> B2  F   flags(cmpl %edi, %ecx) is false   CMP.gt.s %edi, %ecx
E3  B2 -> B1  U   back edge

4 blocks, 4 edges, 1 decision, cyclomatic 2, loop header B1 (depth 1, body B1 B2)
```

The AArch64 and RISC-V hand-written versions of the same function give the same four blocks, four edges, one
decision and one loop — the shape survives the change of architecture, which is the whole point.

## 6. What changed in the shared model

Part 5 touched the shared files in exactly three places, all additive:

* `model/ops.py`: one new operation, **`MOVE`** ("copy a value between registers or load a constant"). Assembly
  has no SSA, so register-to-register copies are a real category that LLVM IR simply does not have.
* `model/cfg.schema.json`: `MOVE` in the operation enum; `asm_file`, `isa`, `syntax` allowed in `meta`.
* `model/cfg.py`: the invariant "the last operation of a block is a terminator" is now **HLL-only**. In
  assembly a block can end merely because the next instruction is a branch target, so its last instruction is
  an ordinary one. Same for "the entry block has no predecessors": an assembly loop may branch back to the
  function's first label.

Everything else — `Node`, `Edge`, `BranchInfo`, JSON, NetworkX, Graphviz, the analysis — is reused unchanged.

## 7. Verification

Deterministic analysis is the verification authority (rule R5), so this part is checked against independent
oracles rather than against its own output. `cfgverify asm-cfg --check` runs all of these:

| Check | What it would catch |
|-------|---------------------|
| JSON schema `cfgverify.cfg/1` | a malformed or out-of-vocabulary CFG |
| JSON and NetworkX round trips | information lost in a representation |
| structural invariants | successors/predecessors disagreeing with the edges, bad indices, exits with successors |
| **compiler block annotations** | a missed branch target, or an instruction misclassified as a transfer |
| **the assembler accepts the file** | we "parsed" something that is not valid assembly for that target |
| **branch targets resolve** | a jump to a label that does not exist in the function |
| cross-check against the Part 4 HLL CFG | decision count and loop count must match the C the file came from |

The compiler-annotation oracle deserves a note, because it is the assembly counterpart of `opt -passes=dot-cfg`
on the HLL side: Clang marks every machine basic block it emits, with a `.LBB<n>_<m>` label or a `# %bb.N:`
comment. Our leaders must be exactly that set, **except** for two differences that are understood and
therefore tolerated:

1. *We split where the compiler did not*, when an LLVM machine block ends with **two** terminators — a
   conditional branch followed immediately by an unconditional one, which AArch64 and RISC-V output does
   constantly. The classic leader algorithm splits after the conditional branch; the extra block contains only
   a jump, and normalization rule N2 will remove it in Part 6.
2. *The compiler split where we did not*, when it kept a machine block that is only reachable by falling
   through. The leader algorithm merges it into its predecessor, which is the same graph.

Any other disagreement is a genuine bug and fails the check.

**Findings worth recording** (things that had to be learned the hard way):

* GCC with `-g` emits labels called `.LBB2:`/`.LBE2:` as well — but those are DWARF **lexical** block markers
  (a C `{ … }` scope), not basic blocks. Matching `.LBB*` loosely made this oracle reject perfectly correct
  GCC output; the pattern must be Clang's exact `.LBB<function>_<block>` form.
* Clang's integrated assembler rejects GCC's own output (`unassigned file number in '.loc' directive`), so the
  assembler check tries `clang --target=…` first and falls back to `gcc -c` for x86-64.
* Assembly does **not** always have at least as many blocks as the IR it came from. `factorial` is 5 blocks in
  IR and 4 in x86-64 assembly, because the leader algorithm merges straight-line IR blocks that the backend
  laid out consecutively. Any check assuming "assembly ≥ IR" is wrong.
* GCC folds a branch on a constant (`do { … } while (1)` becomes a plain `jmp`) where Clang's `-O0` IR still
  carries `br i1 true`. So the GCC graph can legitimately have fewer decisions — exactly as many fewer as the
  HLL CFG has constant-condition branches, which is what normalization rule N8 folds on the HLL side.

`tests/test_asm_cfg.py` (129 tests at the default fuzz count, more with `CFGVERIFY_FUZZ_N`) covers: per-ISA instruction decoding, normalized operations, ISA
detection, the reader on a synthetic sample, the three hand-written files, **every regression and example C
program × every candidate ISA**, GCC output, Intel syntax, the CLI, and randomly generated programs
(`tests/random_c.py`, `CFGVERIFY_FUZZ_N`).

## 8. Checkpoint result

> **Checkpoint: different Assembly files for the supported architecture can be processed using the same CFG
> generator.**

`bash scripts/checkpoint_part5.sh` prints the checksums of the implementation, then feeds it every input
without changing a line of code:

| Inputs | Result |
|--------|--------|
| 15 C programs → clang **x86-64** assembly | 23 CFGs, all checks pass |
| the same 15 → clang **AArch64** assembly | 23 CFGs, all checks pass |
| the same 15 → clang **RISC-V** assembly | 23 CFGs, all checks pass |
| the same 15 → **GCC** x86-64 assembly (a second compiler) | 23 CFGs, all checks pass |
| the same 15 → clang **Intel syntax** (a second syntax) | 23 CFGs, all checks pass |
| 3 **hand-written** files, no compiler directives, `--isa auto` | 3 CFGs, ISA detected and printed, all checks pass |

Same `asm/reader.py`, same `asm/cfg_builder.py`, same checks, same schema — only the `--isa` argument changes.
The checkpoint ends by repeating that the architecture to verify is still **TO CONFIRM WITH PROFESSOR**.
