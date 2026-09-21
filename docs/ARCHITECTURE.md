# Architecture: C/HLL ↔ Assembly Control-Flow Consistency Verifier

> **Status:** Part 0 design, draft v0.1 (2026-09-20). Parts 1–7 are done; see §12.
> Anything marked **TO CONFIRM WITH PROFESSOR** is still open. Every open item is collected in
> [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md), and this document refers to them by ID (for example **[A1]**).
> Anything labelled *design choice* is our own engineering decision. It can be changed and is **not** a requirement from the professor.

---

## 1. The project in simple language

A C program and the assembly generated from it should be two descriptions of the same behaviour. The C code is
written for people. The assembly is written for a processor. If the compiler (or whatever produced the assembly)
did its job, the two should make the **same decisions in the same places**: the same `if`s, the same loops, the same
early `return`s, and they should connect to each other the same way.

Think of two maps of one city. One is the architect's plan (the C program). The other is a survey of the roads that
were actually built (the assembly). The survey uses different street names and draws every lane marking, so the two
maps look nothing alike. But if you keep only the **junctions and the roads between them**, the two maps should have
the same shape. This project automates that comparison:

1. Turn the C program into a map of junctions and roads, called a **control-flow graph (CFG)**. We let the Clang
   compiler translate C into **LLVM IR** first, because the IR makes every jump explicit.
2. Turn the assembly file into the same kind of map.
3. **Normalize** both maps: remove differences that don't matter, such as a road that only leads to another road,
   or a junction that is never reached.
4. **Match** the two maps. The answer is **MATCH** or **MISMATCH**, with exact evidence showing *where* they differ.
5. Optionally, ask an **AI** to explain that evidence in plain English. The AI never decides the answer. It only
   explains what the deterministic checker already found.

---

## 2. Non-negotiable scope rules (from the project brief)

| # | Rule | How the design enforces it |
|---|------|---------------------------|
| R1 | Do not assume x86-64 unless the professor confirms it. | Everything that depends on the ISA sits behind an **ISA plug-in interface** (§7.3). Part 5 implements three *candidate* plug-ins (x86-64 AT&T/Intel, AArch64, RISC-V); **none of them is a default**, the reader and the CFG builder contain no ISA knowledge, and `--isa` must be given on every run. **[A1]/[A2]** remain open. |
| R2 | Do not assume an assembly architecture or toolchain unless it is confirmed. | The assembly producer, the syntax and the ABI are open questions **[A2]–[A5]**. The IR target triple is a parameter, not a constant (§7.1). |
| R3 | Do not invent requirements. | Proposals are labelled *design choice* or *proposal*. Open items are labelled **TO CONFIRM WITH PROFESSOR**. |
| R4 | The deterministic CFG analysis and matcher are the verification authority. | The verdict is computed only in `match/` and `report/verdict.py`. The AI module has no write access to the verdict (§7.8). |
| R5 | AI never decides correctness. It only explains evidence. | The explainer receives a frozen evidence record and its output is checked afterwards. The system works fully without AI. |
| R6 | Start with C and a manageable control-flow subset. | A proposed Phase A subset is in §8 and must be approved **[B3]**. |

---

## 3. Key concepts

### 3.1 C / HLL (high-level language)
The input program, written for humans. It has structured control flow: `if/else`, `while`, `for`, `do-while`,
`break`, `continue` and `return`. The brief says "C/HLL" because, in principle, any language with an LLVM front end
(C++, Rust, Swift, …) could be supported later **[B1]**. We start with **C only**.

### 3.2 LLVM and Clang
**LLVM** is a compiler infrastructure: a set of reusable compiler parts. **Clang** is LLVM's C/C++ front end. It
reads C and produces **LLVM IR**. We use Clang only as a *trusted translator* from C to IR. It is not the thing
being verified.

### 3.3 LLVM IR
LLVM's intermediate representation. It sits between C and assembly: lower level than C (every `if` and loop becomes
explicit jumps), but higher level than assembly (unlimited named variables and no machine registers). The IR is
organised into **functions**, and each function is a list of **basic blocks**. This is exactly the structure a CFG
needs.

A caveat: IR is *mostly* target independent, but Clang builds the **target triple and data layout** into it (for example
`target triple = "x86_64-pc-linux-gnu"`). For the simple control flow in Phase A this rarely changes the CFG, but
the IR should be generated for the **same target as the assembly** once that target is confirmed **[A7]**.

### 3.4 Basic block
A maximal straight-line sequence of instructions that
- is entered only at its **first** instruction, and
- is left only at its **last** instruction (the *terminator*: a jump, a branch or a return).

If one instruction in a block runs, all of them run. Control flow only happens *between* blocks.

### 3.5 Control-flow graph (CFG)
A directed graph with one **node per basic block** and one **edge** A→B for every way control can pass from the end
of A to the start of B. Each function has its own CFG, with one **entry** node and one or more **exit** (return) nodes.

- **HLL CFG**: the CFG built from the LLVM IR of the C program. It stands in for the C program's control flow.
- **Assembly CFG**: the CFG built from the assembly file.

### 3.6 Assembly
The textual form of machine instructions for one specific **ISA** (x86-64, AArch64, RISC-V, MIPS, …) in one
specific **syntax/dialect** (for example GNU `as` AT&T or Intel syntax, NASM, MASM). The rules for finding blocks
depend on the ISA:
- which mnemonics are jumps, conditional branches, calls and returns;
- label and comment syntax;
- delay slots (MIPS), predicated instructions (ARM32), and so on.

That is why ISA knowledge is a plug-in (§7.3).

### 3.7 Normalization
Different but equivalent ways of drawing the same control flow have to be made identical before comparing. One
example: at `-O0`, Clang's IR contains an `if.end` block that only jumps on to another block. The assembly might
merge it away, or keep it. Normalization applies the **same documented, semantics-preserving rewrite rules to both
CFGs**, and each rule application is logged as evidence (§7.4).

### 3.8 CFG matching
Deciding whether the two normalized CFGs have the same structure, and producing a **block-to-block mapping** if they
do. Cheap checks come first (block, edge and loop counts), followed by an exact graph-isomorphism check (§7.5).

### 3.9 Mismatch detection
When the graphs do not match, the matcher does more than say "no". It locates and classifies the difference (a
missing back edge, an extra branch, a missing return path, …) and records it as a structured **finding** with
references to IR blocks, assembly lines and, if available, C source lines (§7.6).

### 3.10 AI explanation
An optional last stage. A language model receives the **evidence record** (the verdict, the findings and the
mapping) and writes a human-readable explanation. It cannot change the verdict, and its output is checked against
the evidence (§7.8).

### 3.11 Worked example (for illustration only; the target is **not** assumed)

```c
int max(int a, int b) {
    if (a > b)
        return a;
    return b;
}
```

LLVM IR from `clang -O0 -S -emit-llvm -fno-discard-value-names` (Clang 18.1.3), with the bodies cut down to show control flow:

```llvm
entry:    ...  %cmp = icmp sgt i32 %0, %1
               br i1 %cmp, label %if.then, label %if.end
if.then:  ...  br label %return
if.end:   ...  br label %return
return:   ...  ret i32 %4
```

HLL CFG: `entry → {if.then, if.end}`, `if.then → return`, `if.end → return`. That is 4 nodes and 4 edges, shaped
like a diamond.

The same function compiled by Clang 18 at `-O0` for **two different ISAs**. Both are shown only to demonstrate
why the design is ISA-neutral:

| x86-64 (AT&T syntax) | RISC-V 64 |
|---|---|
| `cmpl -12(%rbp), %eax` | `bge a0, a1, .LBB0_2` |
| `jle .LBB0_2` ← **condition inverted** (IR: `a > b`) | `j .LBB0_1` ← explicit jump |
| `# %bb.1:` ← **block without a label** (fall-through) | `.LBB0_1:` |
| `... jmp .LBB0_3` | `... j .LBB0_3` |
| `.LBB0_2: ...` (falls through) | `.LBB0_2: ... j .LBB0_3` |
| `.LBB0_3: ... retq` | `.LBB0_3: ... ret` |

Both produce the same 4-node diamond, but getting there means:

- finding blocks that have **no label** (the leader algorithm, §7.3);
- handling **fall-through** edges;
- **not** assuming the IR's true/false edge lines up with the assembly's taken/not-taken edge, because compilers
  invert conditions freely.

These are observations from our own probe runs, not general guarantees.

---

## 4. Inputs and outputs

### Inputs
| Input | Description | Status |
|---|---|---|
| C source file | A single `.c` translation unit that Clang can compile. | Single file vs. multi-file: **[B2]**. C standard: **[B1]**. |
| Assembly file | A textual assembly file that claims to be the translation of that C file. | ISA **[A1]**, syntax **[A2]**, producer **[A3]**, optimization level **[A4]**, ABI/object format **[A5]**. |
| Configuration (optional) | Target triple, function filter, which normalization rules to use, output directory. | *Design choice.* |

### Outputs
| Output | Description | Status |
|---|---|---|
| Verdict | **MATCH** / **MISMATCH** per function and overall. | Proposed third state **UNSUPPORTED** for out-of-subset input **[C3]**. Per-function granularity **[C4]**. |
| Evidence report | Machine-readable JSON: CFGs, normalization log, mapping, findings. | *Design choice.* Other formats **[D1]**. |
| Human report | Text/Markdown (HTML later). | **[D1]** |
| CFG visualizations | DOT/SVG: HLL vs. assembly, before and after normalization, mapped blocks, highlighted mismatches. | **[D2]** |
| AI explanation | Optional, labelled "non-authoritative". | Required or optional, and which model: **[E1]** |

---

## 5. Architecture and data flow

```
            C / HLL source (.c)                          Assembly (.s)
                   │                                           │
                   ▼                                           ▼
   [hll/frontend]  Clang: C → LLVM IR (.ll)        [asm/reader]  text → labels, directives,
                   │                                           │              instructions
                   ▼                                           ▼
   [hll/ir_reader] IR → functions → basic blocks   [asm/isa/<ISA>] classify: branch / jump /
                   │                                           │     call / return / other
                   ▼                                           ▼
   [hll/cfg_builder]  HLL CFG (per function)      [asm/cfg_builder]  leaders → blocks → ASM CFG
                   │                                           │
                   ▼                                           ▼
   [normalize]  same rule set, logged  ◄───────►  [normalize]  same rule set, logged
                   │                                           │
                   └─────────────────┐       ┌─────────────────┘
                                     ▼       ▼
                          [match]  function pairing → invariants
                                   → isomorphism/mapping → diagnosis
                                              │
                                              ▼
                          [report/verdict]  MATCH / MISMATCH (+ evidence JSON, SVGs)
                                              │   (read-only)
                                              ▼
                          [explain]  AI explanation of the evidence (optional, non-authoritative)
```

| Stage | Input → Output | Module | Deterministic? |
|---|---|---|---|
| 1. Front end | `.c` → `.ll` | `hll/frontend.py` | Yes, for a pinned Clang version and flags |
| 2. IR reading | `.ll` → functions, blocks, terminators | `hll/ir_reader.py` | Yes |
| 3. HLL CFG | blocks → `CFG` | `hll/cfg_builder.py` | Yes |
| 4. Asm reading | `.s` → instruction stream | `asm/reader.py` + `asm/isa/*` | Yes |
| 5. Asm CFG | instruction stream → `CFG` | `asm/cfg_builder.py` | Yes |
| 6. Normalization | `CFG` → normalized `CFG` + rule log | `normalize/rules.py` | Yes |
| 7. Matching | two normalized CFGs → mapping or findings | `match/*` | Yes |
| 8. Verdict and report | findings → verdict + evidence | `report/*` | Yes |
| 9. Explanation | evidence → prose | `explain/explainer.py` | **No**, and therefore **advisory only** |

`pipeline.py` wires the stages together, and `cli.py` exposes them.

---

## 6. Module responsibilities and directory structure

```
btp-cfg-verifier/
├── README.md                 How to set up, run and check the Part 1 checkpoint
├── requirements.txt          Pinned Python dependencies
├── pyproject.toml            Package metadata, `cfgverify` command, pytest config
├── Makefile                  setup / env / checkpoint / test / clean shortcuts
├── docs/
│   ├── ARCHITECTURE.md       This document (Part 0)
│   ├── OPEN_QUESTIONS.md     Register of items TO CONFIRM WITH PROFESSOR
│   ├── ENVIRONMENT.md        Part 1: every tool, why it is needed, how to check it
│   ├── CONTROL_FLOW_FUNDAMENTALS.md  Part 2: each C construct → blocks, edges, conditions, back edges, CFG
│   ├── LLVM_IR_GUIDE.md      Part 3: IR generation, IR concepts, construct → IR mapping, the IR reader
│   ├── HLL_CFG.md            Part 4: the HLL CFG generator (nodes, operations, edges, JSON/NetworkX/Graphviz)
│   └── cfg/                  Expected-CFG diagrams (SVG), generated from regression/expected
├── scripts/
│   ├── setup_wsl.sh          Installs the toolchain on Ubuntu (WSL2), creates .venv
│   ├── checkpoint_part1.sh   Runs the C → LLVM IR checkpoint end to end
│   ├── check_part2.sh        Checks the regression programs' expected CFGs against LLVM
│   ├── checkpoint_part3.sh   New C file → IR → blocks/edges, cross-checked, reader unchanged
│   └── checkpoint_part4.sh   Many C files → HLL CFGs (JSON/SVG/gallery) with one implementation, all checked
├── examples/c/               Small C programs: hello_cfg.c (Part 1), part3/ (checkpoint inputs, written after the reader was frozen)
├── regression/
│   ├── c/                    Part 2 regression programs, one per construct (not an input limit)
│   └── expected/             Hand-derived expected CFG of each function (TOML), checked against LLVM
├── tests/                    pytest suite (Part 1: environment and C → IR tests)
├── build/                    Generated files (IR, DOT, SVG, toolchain.json); git-ignored
└── src/cfgverify/
    ├── cli.py                Command-line entry point (`cfgverify ...`)
    ├── config.py             Single source of truth for compiler flags and paths
    ├── toolchain.py          Find external tools and record their versions      [Part 1]
    ├── graph_utils.py        Dominators, back edges, natural loops, cyclomatic   [Part 2]
    ├── expected.py           Load, draw and document the expected CFGs           [Part 2]
    ├── pipeline.py           Orchestrates stages 1–9                           [stub]
    ├── model/
    │   ├── ops.py            Normalized-operation vocabulary (shared HLL/assembly)        [Part 4]
    │   ├── cfg.py            CFG / Node / Edge / BranchInfo, analysis, JSON, NetworkX     [Part 4]
    │   ├── cfg.schema.json   JSON Schema "cfgverify.cfg/1"                                [Part 4]
    │   ├── cfg_dot.py        Graphviz rendering + HTML gallery                            [Part 4]
    │   └── evidence.py       Finding, Mapping, FunctionResult, Verdict             [Part 7]
    ├── hll/
    │   ├── frontend.py       C → LLVM IR using Clang with canonical flags       [Part 1]
    │   ├── llvm_view.py      LLVM's own CFG (opt dot-cfg), used as a reference oracle   [Part 1]
    │   ├── ir_reader.py      LLVM IR text → functions/blocks/instructions/terminators/edges   [Part 3]
    │   ├── ir_crosscheck.py  Reader vs. opt dot-cfg, LLVM preds comments, llvmlite           [Part 3]
    │   ├── ir_ops.py         IR instruction → normalized operation (canonical names)     [Part 4]
    │   ├── cfg_builder.py    IR function → HLL CFG (nodes, edges, branch info, analysis)  [Part 4]
    │   └── cfg_checks.py     Schema, round trips, invariants, agreement with LLVM         [Part 4]
    ├── asm/
    │   ├── reader.py         Assembly text → labels/directives/instructions (ISA-neutral)  [Part 5]
    │   ├── cfg_builder.py    Leader algorithm → ASM CFG (nodes, edges, branch info)        [Part 5]
    │   ├── cfg_checks.py     Schema, round trips, invariants, compiler block marks,
    │   │                     the assembler, branch-target resolution                       [Part 5]
    │   └── isa/
    │       ├── base.py       ISA plug-in contract (what an ISA must describe)              [Part 5]
    │       ├── x86.py        CANDIDATE: x86-64, AT&T and Intel syntax                      [Part 5]
    │       ├── aarch64.py    CANDIDATE: AArch64                                            [Part 5]
    │       ├── riscv.py      CANDIDATE: RISC-V                                             [Part 5]
    │       └── __init__.py   ISA registry + detection. NO DEFAULT ISA: [A1]/[A2] still open
    ├── normalize/
    │   ├── semantics.py      Instruction -> semantic category (the brief's vocabulary)  [Part 6]
    │   ├── conditions.py     Branch condition -> comparable form (relation, shape, polarity)  [Part 6]
    │   ├── loops.py          Loop nest -> comparable signature, with no block names   [Part 6]
    │   ├── rules.py          Normalization rules N1-N8 + replayable rule log          [Part 6]
    │   └── checks.py         Proof that meaning survived normalization (P1-P6)        [Part 6]
    ├── match/
    │   ├── invariants.py     L1 fingerprints + the name-free block role            [Part 7]
    │   ├── matcher.py        L0 pairing, L2/L3 isomorphism, partial mapping, L4    [Part 7]
    │   ├── diagnose.py       Localize and classify mismatches into findings        [Part 7]
    │   └── __init__.py       verify(): the whole pipeline in one call              [Part 7]
    ├── report/
    │   ├── verdict.py        THE ONLY PLACE A VERDICT IS DECIDED (rule R4)         [Part 7]
    │   └── render.py         Terminal summary + Markdown report + JSON evidence    [Part 7]
    └── explain/explainer.py  AI explanation with guardrails                    [stub]
```

**Ownership rule (design choice):** only `report/verdict.py` may create a `Verdict`. It consumes `match/` output
and nothing from `explain/`.

---

## 7. Stage design

### 7.1 HLL front end (`hll/frontend.py`)
- Command: `clang -S -emit-llvm -O0 -fno-discard-value-names [--target=<triple>] file.c -o file.ll`.
- `-O0`: keeps the IR structurally close to the source. The C-level `if`/`for`/`while` shapes survive, with
  readable block names such as `for.cond` and `if.then`.
- `-fno-discard-value-names`: release builds of Clang otherwise rename blocks to `%1`, `%2`, …, which makes
  evidence much harder to read.
- `--target`: defaults to the host (the WSL host is `x86_64-pc-linux-gnu`). Once **[A1]/[A7]** are confirmed, the
  target must be set to the assembly's target. The host default is **not** an assumption about the verified ISA.
- Later: `-g` adds `!dbg` source locations to IR blocks. These let evidence point at C line numbers (*design choice*).
- IR at higher optimization levels is **not** used as the reference, because that would mean trusting the
  optimizer we might be checking. How to handle optimized assembly: **[A4]**.

### 7.2 IR → basic blocks → HLL CFG (`hll/ir_reader.py`, `hll/cfg_builder.py`)
- Edges come from each block's **terminator**:
  - `br label %X` → one edge.
  - `br i1 %c, label %T, label %F` → two edges, labelled `true`/`false`.
  - `ret` → edge to a virtual EXIT node.
  - `unreachable` → sink.
  - `switch` → N edges (outside Phase A).
  - `indirectbr` and `callbr` → UNSUPPORTED in Phase A.
- **Calls are not terminators**, because the CFG is intraprocedural **[C2]**.
- **Reader (design choice, revised in Part 3):** a dedicated **text reader** for `.ll` files
  (`hll/ir_reader.py`, documented in [`LLVM_IR_GUIDE.md`](LLVM_IR_GUIDE.md) §11).
  - *Original Part 0 proposal:* llvmlite as the reader.
  - *Why it changed:* llvmlite bundles a second LLVM (22 vs. Clang 18) and hides the `.ll` line numbers, printed
    names and `!dbg` lines that evidence needs.
  - *Pitfall to remember:* llvmlite's in-memory operand order for a conditional `br` is (false, true). The text
    order is (true, false).
- **Oracles (design choice):** three independent LLVM views must agree with the reader (`hll/ir_crosscheck.py`):
  - `opt -passes=dot-cfg`, compared edge by edge with successor indices;
  - LLVM's `; preds =` comments;
  - llvmlite's parse, compared by opcode sequence per block.
  The same dot-cfg view also checks the Part 2 expected CFGs.

### 7.3 Assembly → ASM CFG (`asm/reader.py`, `asm/isa/*`, `asm/cfg_builder.py`)
**ISA-neutral part (the classic leader algorithm):**
1. Split into functions. On ELF/GNU this uses `.globl`/`.type f,@function`/`.size`, but the conventions depend on
   **[A5]**, and hand-written assembly may have none of these **[A3]**.
2. Remove comments and non-code directives. Keep labels, and keep `.loc` as an optional hint **[B5]**.
3. **Leaders** are: the first instruction; every branch or jump target; and every instruction right after a branch,
   jump or return.
4. A block runs from one leader up to the next.
5. Edges:
   - conditional branch → target + fall-through;
   - unconditional jump → target;
   - return → EXIT;
   - call → fall-through (not a block end);
   - indirect jump → UNSUPPORTED in Phase A (jump tables come later).

**ISA plug-in contract (`asm/isa/base.py`)**, which each confirmed ISA must provide:
- the comment and label syntax, and how local labels look (for example `.LBB0_3`, `1:` with `1f`/`1b`);
- a mnemonic → instruction-class table: `COND_BRANCH`, `JUMP`, `INDIRECT_JUMP`, `CALL`, `RETURN`, `OTHER`;
- how to extract branch targets from operands;
- ISA quirks the block builder must respect:
  - **delay slots** (MIPS/SPARC), where the block ends *after* the delay-slot instruction;
  - **predication** (ARM32 conditional execution, Thumb IT blocks);
  - multi-instruction condition sequences (for example x86 floating-point `==` compiles to `jne` + `jp`, so one IR
    branch becomes two assembly branches, which we saw in a probe);
  - 64-bit compares on 32-bit targets, which split into several branches;
- later: condition-code semantics, needed for polarity checks (§7.5, L4).

**Status: implemented in Part 5** — see [`ASM_CFG.md`](ASM_CFG.md). The ISA-neutral reader and leader algorithm
above are `asm/reader.py` and `asm/cfg_builder.py`; the plug-in contract is `asm/isa/base.py`, and three
**candidate** plug-ins exist (`x86.py` for AT&T and Intel, `aarch64.py`, `riscv.py`). Building candidates rather
than committing to one is how rule R1 is honoured: no ISA is the default, `--isa` is required on every run
(`--isa auto` detects and *prints* its decision together with the scores), and every CFG records
`meta.architecture_status = "TO CONFIRM WITH PROFESSOR ([A1], [A2]); this ISA was selected explicitly"`.
When **[A1]/[A2]** are answered, the confirmed plug-in stays and the others simply stop being exercised; a new
architecture is one new file of tables and no change anywhere else.

Two shared-model consequences of reading assembly (Part 5): a new normalized operation **`MOVE`** (assembly has
register-to-register copies; LLVM IR in SSA form does not), and the invariant "the last operation of a block is a
terminator" is now HLL-only, because an assembly block can end merely because the next instruction is a branch
target.

### 7.4 Normalization (`normalize/rules.py`)
All rules are **semantics-preserving for control flow**, applied **identically to both CFGs** until nothing changes,
and **logged** (`NormalizationStep` records go into the evidence). *Proposal, to be approved in* **[C5]**:

| Rule | Rewrite | Why it is needed |
|---|---|---|
| N1 Unreachable removal | Delete blocks that cannot be reached from the entry. | Dead blocks left by either side |
| N2 Jump-only bypass | A block whose only content is an unconditional jump is removed, and its predecessors are retargeted. | Clang `-O0` `if.end`-style blocks; assembly jump pads |
| N3 Chain merge | Merge A→B when A has exactly one successor and B has exactly one predecessor (and B is not the entry). | Label or fall-through splits that don't affect control flow |
| N4 Single EXIT | Every return goes to one virtual EXIT node. | Multiple epilogues or return sites |
| N5 Degenerate branch | A conditional branch with both targets equal becomes a jump. | Equivalent encodings |
| N6 Polarity-free edges (stage 1) | Conditional edges are compared as an unordered pair at first. | Compilers invert conditions (see §3.11) |
| N7 Noreturn sink | Blocks ending in `unreachable` or a known noreturn call go to a TRAP sink. | `exit()`/`abort()` handling (Phase B, **[B3]**) |
| N8 Constant-condition branch | `br i1 true/false` becomes an unconditional jump to the live target. | `do … while (1)` gives `br i1 true` in Clang `-O0` IR (Part 3, P1) and a plain `jmp` in GCC's assembly. |

**Status: implemented in Part 6** — see [`NORMALIZATION.md`](NORMALIZATION.md). All eight rules run to a
fixpoint, on **both** CFGs with one implementation (`normalize/rules.py` never reads `cfg.origin`, the ISA or
a file name, and a test asserts that from the source), and every application is logged as a
`NormalizationStep` with the raw blocks behind it. N6 is implemented as an annotation rather than a rewrite:
each conditional edge carries its comparable `condition_key` and the `polarity_free_key` that ignores which
arm the compiler chose. Part 6 also added the layer the rules work in: **semantic operations**
(`normalize/semantics.py` — ASSIGN, ADD, COMPARE, CONDITIONAL_BRANCH, …), **comparable branch conditions**
(`normalize/conditions.py`) and **comparable loop signatures** (`normalize/loops.py`).
`normalize/checks.py` proves per run that the rewriting kept the control-flow meaning (properties P1–P6).
A possible **N9** (constant propagation) is recorded as finding F2 in NORMALIZATION.md §6, and the whole rule
set remains a proposal under **[C5]**.

N6 is a known weakness, because it hides swapped branch arms. See §10 and L4.

### 7.5 Matching (`match/*`)
1. **L0 Function pairing:** match functions by symbol name. Platform decoration (such as a leading `_` on
   Mach-O/Windows) depends on **[A5]**. Missing or extra functions become findings **[B4]**.
2. **L1 Invariants (fast reject):** compare node count, edge count, cyclomatic complexity (E − N + 2), the number of
   2-way branch nodes, the number of return paths, the number of natural loops (back edges via the dominator tree),
   and the maximum loop nesting depth.
3. **L2 Exact structure:** directed-graph isomorphism with the entry fixed to the entry and EXIT fixed to EXIT, and
   node attributes (out-degree, loop-header flag, loop depth) required to agree. We use `networkx` VF2. CFGs are
   small, so exact matching is practical. The result is a **block mapping**.
4. **L3 Anchors (optional):** block "content signatures" (calls made, constants, return presence) and `!dbg`/`.loc`
   line numbers. These choose between several possible isomorphisms and strengthen the mapping.
5. **L4 Polarity check (later phase):** for each mapped 2-way branch, recover the IR predicate (for example
   `icmp sgt`) and the assembly condition (for example `jle` after `cmp`), and check that true/false edges map to the
   correct taken/fall-through edges. Whether L4 is required depends on what "consistent" means **[C1]**.

**Status: implemented in Part 7** — see [`MATCHING.md`](MATCHING.md). Three corrections the design needed, each
found by running the matcher against real compiler output, and each one a false-MISMATCH source if ignored:
L2 must **not** require `T` to map to `T` (compilers invert a test and swap the arms; the polarity-free key of
rule N6 is what makes AArch64 and RISC-V match at all), must **not** fold the branch condition into the
structural test (a compiler may emit `x >= 2` for `x > 1`), and L3 must compare block content by
**containment as a tie-breaker**, never by equality — the assembly always does more work than the IR.
L4 runs and reports; whether its findings are errors is the `--require-conditions` switch, i.e. **[C1]**.
When the graphs are not isomorphic, `match/matcher.py:partial_mapping()` still produces a mapping by colour
refinement, content anchors and growth, so `match/diagnose.py` can localize the difference instead of
declaring every block missing.

### 7.6 Mismatch detection and evidence (`match/diagnose.py`, `model/evidence.py`)
Every finding has an ID, a kind, a function, the HLL nodes, the assembly nodes, the assembly line range, C source lines
(if available) and a deterministic one-line description. Proposed kinds:

| Kind | Example |
|---|---|
| `FUNCTION_MISSING` / `FUNCTION_EXTRA` | `helper` is in C but not in the assembly |
| `BLOCK_COUNT` / `EDGE_COUNT` / `CYCLOMATIC` | 7 vs. 6 decisions |
| `LOOP_MISSING` / `LOOP_EXTRA` / `LOOP_NESTING` | The back edge of `for.inc → for.cond` has no counterpart |
| `BRANCH_ARITY` | A 2-way branch in HLL corresponds to a 1-way block in the assembly |
| `EXIT_PATHS` | The C code returns on 3 paths; the assembly returns on 2 |
| `UNMAPPED_REGION` | The smallest HLL subgraph with no assembly counterpart (from the maximum common subgraph) |
| `POLARITY` (L4) | The branch arms appear to be swapped |
| `UNSUPPORTED_CONSTRUCT` | Indirect jump, jump table, inline asm, … |

**Status: implemented in Part 7.** The finding vocabulary is now `model/evidence.py`, and it is the ten kinds
the brief names (`MISSING_BLOCK`, `EXTRA_BLOCK`, `MISSING_EDGE`, `EXTRA_EDGE`, `WRONG_BRANCH`,
`WRONG_BRANCH_CONDITION`, `MISSING_LOOP_BACK_EDGE`, `UNEXPECTED_PATH`, `MISSING_RETURN`,
`STRUCTURAL_MISMATCH`) plus `FUNCTION_MISSING`/`FUNCTION_EXTRA` **[B4]**, `UNSUPPORTED_CONSTRUCT` **[C3]**
and `AMBIGUOUS_MAPPING`. Every kind is produced from real inputs by the mutation tests; none is theoretical.

### 7.7 Verdict (`report/verdict.py`)
- **MATCH:** every paired function passes L1 and L2 (and L4, if **[C1]** requires it), and there are no
  function-level findings.
- **MISMATCH:** at least one finding of a mismatch kind.
- **UNSUPPORTED** (*proposal* **[C3]**): the input uses constructs outside the approved subset. The tool refuses to
  guess, because a wrong MATCH is the worst possible error for a verifier.

### 7.8 AI explanation (`explain/explainer.py`)
Guardrails (*design choice* that implements R4/R5):
1. **Input is only the frozen evidence JSON** plus the relevant source and assembly excerpts. The model gets no tool
   access and cannot re-run anything.
2. **The verdict is printed by deterministic code first.** The AI text goes in a separate section labelled
   "AI explanation (non-authoritative)".
3. **Post-checks:** the explanation must cite existing finding IDs only, and must not state a different verdict.
   If it fails either check, it is discarded and replaced with a deterministic template explanation.
4. **The system works fully offline without AI.** Template explanations are generated from finding kinds.
5. Provider and model (cloud API vs. local model), cost and data policy: **[E1]**.

---

## 8. Proposed initial supported subset (Phase A)

*Proposal, requires approval* **[B3]**.

**In:** one `.c` file; functions with bodies; sequential code; `if`, `if/else` and `else if` chains; `while`,
`do-while` and `for`; `break` and `continue`; `return`, including early returns; nesting of all of these; `&&`,
`||` and `!` in conditions (short-circuiting creates real branches); `?:`; calls to other functions (treated as
ordinary instructions); **integer** conditions of native word size or smaller.

**Out (for now), with reasons:**
- `switch`: can become a jump table (indirect jump) or a comparison tree. Planned for Phase B.
- `goto`: planned for Phase B, since it can produce irreducible CFGs.
- `setjmp`/`longjmp`, signals, inline asm, computed goto (GNU `&&label`): not expressible as a static CFG, or ISA-specific.
- **Floating-point** conditions: extra unordered-comparison branches on some ISAs (see §7.3).
- Integer types **wider than the target word**: multi-branch compare sequences.
- `exit()`/`abort()` and other noreturn calls: Phase B, together with N7.

---

## 9. Assumptions register

| ID | Assumption | Type |
|---|---|---|
| S1 | The unit of comparison is **one function's intraprocedural CFG**. | Design choice; confirm **[C2]** |
| S2 | IR from Clang at `-O0` is a faithful stand-in for the C program's control flow. | Design choice (standard practice) |
| S3 | The assembly is plain text, not a binary. Disassembling binaries is out of scope. | Needs confirmation **[A3]** |
| S4 | The development machine is Windows x64 with **WSL2 Ubuntu 24.04** (Clang/LLVM 18.1.3 from apt). This concerns the dev environment only and does not choose the verified ISA. | Agreed with the student |
| S5 | Implementation language is Python 3.12. External tools: clang/opt (LLVM), graphviz. | From the brief (Part 1) |
| S6 | Function names are the pairing key between C and assembly. | Design choice; confirm **[A5]/[B4]** |
| S7 | The verdict is structural (L1/L2) until **[C1]** decides whether polarity or condition checks are required. | Needs confirmation **[C1]** |

---

## 10. Known limitations (to discuss openly)

1. **Matching structure is necessary, but it is not proof of equivalence.** Two isomorphic CFGs can still differ in
   conditions or data. For example, if both arms of an `if` are single blocks, swapping the arms keeps the graph
   isomorphic. L3 anchors and L4 polarity checks reduce this gap, but only up to the depth agreed in **[C1]**.
2. **Optimized assembly legitimately changes CFG shape:**
   - loop rotation, where `while` becomes guarded `do-while`;
   - unrolling;
   - if-conversion to conditional moves;
   - tail duplication;
   - inlining.
   A correct `-O2` translation can therefore be reported as MISMATCH unless extra normalizations are added.
   In a probe on x86-64, Clang 18 at `-O2` turned a function's `if / else if` chain and an in-loop `if` into `cmov`
   instructions, so those branches no longer existed, and it restructured the loops as well. Scope: **[A4]**.
3. **Toolchain versions change IR text.** Versions are recorded in `build/toolchain.json` and pinned in
   `requirements.txt`.

---

## 11. Evaluation strategy (proposal)

- **Positive pairs:** C programs from the Phase A subset, with assembly produced by the confirmed producer **[A3]**
  for the confirmed target. Expected result: MATCH.
- **Negative pairs via controlled mutation:** automatically edit correct assembly to retarget a branch, delete a
  back edge, invert a condition, drop a block or duplicate a path. Expected result: MISMATCH, with the right finding kind.
- **Metrics:** false MATCH rate (the critical one), false MISMATCH rate, and whether the finding points to the right place.
- **Oracle check:** our HLL CFG must equal LLVM's `dot-cfg` graph for every test program.
- Test set supplied by the professor: **[F1]**.

## 12. Roadmap

Part numbers follow the project brief. Items marked *proposal* are our suggestions for the parts the brief has not
defined yet. They are for discussion and are not requirements.

| Part | Content | Status / blocked by |
|---|---|---|
| 0 | Project understanding and design (this document) | Done |
| 1 | Development environment; C → LLVM IR checkpoint | Done |
| 2 | C/HLL control-flow fundamentals + regression programs ([`CONTROL_FLOW_FUNDAMENTALS.md`](CONTROL_FLOW_FUNDAMENTALS.md)) | Done |
| 3 | LLVM IR generation and parsing: IR reader + cross-checks ([`LLVM_IR_GUIDE.md`](LLVM_IR_GUIDE.md)) | Done |
| 4 | HLL CFG generator: CFG data model, normalized operations, JSON/NetworkX/Graphviz ([`HLL_CFG.md`](HLL_CFG.md)) | Done |
| 5 | Assembly CFG generator: ISA-neutral reader, ISA plug-in contract, leader algorithm, 3 candidate ISAs ([`ASM_CFG.md`](ASM_CFG.md)) | Done. The ISA to *verify* is still [A1]/[A2] |
| 6 | CFG normalization: semantic operations, comparable conditions and loops, rules N1-N8 + rule log ([`NORMALIZATION.md`](NORMALIZATION.md)) | Done. The rule set is still a proposal, [C5] |
| 7 | CFG matching and verification: L0-L4, located findings, verdict, reports ([`MATCHING.md`](MATCHING.md)) | Done. What "consistent" means is still [C1] |
| *proposal* | Mutation-based evaluation | [F1] |
| *proposal* | AI explanation with guardrails | [E1] |
| *proposal* | More constructs (`&&`/`||`, `?:`, switch, goto, noreturn), L3 anchors, L4 polarity | [B3], [C1] |
