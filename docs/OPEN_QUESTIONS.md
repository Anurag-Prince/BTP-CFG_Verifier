# Open questions: TO CONFIRM WITH PROFESSOR

Every item below is **unresolved**. The design does not assume any answer. Where work has to go on in the meantime,
the "Until confirmed" column states the neutral placeholder we use. A placeholder is **not** a decision.

Status legend: `OPEN` = not yet asked · `ASKED` = raised, waiting for an answer · `CONFIRMED` = answered (record the answer and date).

Suggested order for the first meeting: **A1, A3, A4, C1**. These four decide most of the remaining design.

---

## A. Target, assembly and toolchain

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| A1 | **Which ISA should the assembly be for?** (x86-64, x86-32, AArch64, ARM32/Thumb, RISC-V 32/64, MIPS, a teaching ISA, several?) | Decides which instructions are branches, jumps, calls and returns, plus quirks such as delay slots, predication and compare sequences. | Part 5: three **candidate** plug-ins (x86-64, AArch64, RISC-V) and no default; `--isa` is required on every run. A candidate is not a decision. | OPEN |
| A2 | **Which assembly syntax/dialect?** (GNU `as` AT&T or Intel, NASM, MASM, ARM UAL, …) | Parser grammar, comment and label syntax, operand order. | Part 5: GNU-as AT&T and Intel are both read by the same generator. Syntax lives in the plug-in. | OPEN |
| A3 | **Who or what produces the assembly?** Clang `-S` from the same C file? GCC? A course or student compiler? Hand-written? Deliberately mutated test files? Is the input always text (`.s`), never a binary? | Decides how regular the input is, whether directives or debug info exist, and what "generated low-level program" means. | Part 5 reads Clang output, GCC output and hand-written files with no directives at all, and never depends on a directive being present. | OPEN |
| A4 | **Which optimization level(s)** will the assembly be generated at? `-O0` only, or also `-O1`/`-O2`/`-O3`? | Optimizations legitimately change CFG shape (if-conversion to `cmov`, loop rotation, unrolling, inlining). Supporting them needs many more normalization rules. | Design around `-O0`-like assembly. | OPEN |
| A5 | **Which OS/ABI/object format** conventions? (ELF/Linux, Windows PE/COFF, Mach-O) | Function boundary directives, local label style (`.LBB0_3` vs `LBB0_3` vs `$LN3@f`), symbol decoration (leading `_`). | Kept inside the plug-in (comment chars, local-label prefixes). Only ELF/Linux is exercised so far. | OPEN |
| A6 | Is a **specific Clang/LLVM version** required? | IR text changes between LLVM versions. | Use Ubuntu 24.04's Clang/LLVM 18.1.3 and record versions in `build/toolchain.json`. | OPEN |
| A7 | Should the reference IR be generated **for the same target triple as the assembly**? (We recommend yes.) | Clang builds target details into the IR, which can occasionally affect lowering. | IR uses the host triple for Part 1 only. `--target` is a parameter. | OPEN |

## B. Inputs and supported language

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| B1 | Is the HLL **C only**, or should other LLVM front ends (C++, Rust, …) be supported later? Which **C standard** (C99/C11/C17)? | Front-end flags, name mangling (C++), scope. | C only, Clang's default standard. | OPEN |
| B2 | **One `.c` file** per test, or multi-file programs? Are headers and standard library calls allowed? | Build handling, and function pairing across files. | One translation unit. | OPEN |
| B3 | **Approve or modify the Phase A control-flow subset** (ARCHITECTURE §8). Specifically: `switch`? `goto`? `exit()`/noreturn calls? floating-point conditions? | Keeps the first milestone manageable, as the brief requires. | Phase A as proposed. | OPEN |
| B4 | Does the assembly always contain **every function** of the C file, and only those? Or can there be extra helpers or missing (inlined) functions? | Function pairing rules and the `FUNCTION_MISSING`/`FUNCTION_EXTRA` findings. | Report differences as findings; never ignore them silently. | OPEN |
| B5 | Will the assembly contain **debug info** (`.loc`/`.file`) or compiler comments? May we use them? | They give strong anchors for mapping and explanations. | Part 5 reads `.loc` into `source_line`/`source_text` and Clang's `# %bb.N` marks, but only as hints and as a cross-check oracle — never as the basis of a verdict. | OPEN |

## C. What "consistent control-flow semantics" means

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| C1 | **Which level of consistency is required?** (a) normalized CFG isomorphism per function; (b) (a) + loop structure; (c) (b) + branch polarity (true/false arms map correctly); (d) (c) + branch condition equivalence. | This is the core definition of the project. Structure alone cannot detect swapped `if` arms when both arms have the same shape (ARCHITECTURE §10). | Part 7 implements (a)-(c) by default and (d) behind `--require-conditions`; branch-condition disagreements are reported either way, as warnings or as errors. | OPEN |
| C2 | **Per-function (intraprocedural) CFG only**, or is interprocedural flow (call graph, call order) also in scope? | Scope of the model and the matcher. | Per function; calls are ordinary instructions. | OPEN |
| C3 | Must the output be strictly **MATCH/MISMATCH**, or may the tool answer **UNSUPPORTED** when input is outside the subset? | Avoids forced guesses, since a false MATCH is the worst possible error. | Part 7 answers UNSUPPORTED by default and MISMATCH under `--strict`; the construct is always named in a finding. | OPEN |
| C4 | Verdict **per program only**, or **per function** as well? | Report design. | Both (per function, then aggregated). | OPEN |
| C5 | **Approve or modify the normalization rules** N1–N8, and the semantic operation vocabulary (the brief's 14 categories plus the 13 extensions in `NORMALIZATION.md` §1). Should a constant-propagation rule **N9** be added (finding F2: Clang keeps `do { } while (1)` as a branch on a register holding 1)? | Normalization decides what counts as the same control flow, so every later verdict depends on it. | N1–N8 as implemented in Part 6, each one switchable and logged; the extension categories are marked as extensions everywhere. | OPEN |

## D. Interface and outputs

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| D1 | **CLI only**, or also a web/GUI front end? Which report formats (JSON, Markdown, HTML, PDF)? | Effort and demo planning. | CLI + JSON + Markdown. | OPEN |
| D2 | Visualization expectations: side-by-side CFGs, mapped nodes coloured, mismatches highlighted? | Output design. | DOT/SVG via Graphviz. | OPEN |

## E. AI explanation

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| E1 | Is the AI explanation **required or optional**? Which model or provider (cloud API or a local model)? Any restrictions on sending code to external services? Budget? | Architecture of `explain/`, cost, privacy. | Part 8 ships a DETERMINISTIC explanation that needs no model at all, plus an optional provider hook whose output is post-checked and discarded if it cites a finding that does not exist or states a different verdict. No provider is configured and none is shipped. | OPEN |
| E2 | Who is the audience for explanations (students, graders, compiler developers), and how much detail do they need? | Prompt and report design. | General technical audience. | OPEN |

## F. Evaluation, platform and deliverables

| ID | Question | Why it matters | Until confirmed | Status |
|---|---|---|---|---|
| F1 | Will the professor provide **test programs or assembly**? What evaluation is expected (number of programs, metrics, mutation testing)? | Evaluation plan (ARCHITECTURE §11). | Build our own Phase A corpus + mutation tests. | OPEN |
| F2 | Milestones and deliverables (report, demo, presentation dates)? | Planning. | None assumed. | OPEN |
| F3 | Is **Linux/WSL2** acceptable for the final demo, or must the tool run natively on Windows? | Toolchain choice (we develop on WSL2 Ubuntu 24.04). | WSL2 Ubuntu 24.04. | OPEN |

---

### Answer log

| Date | ID | Answer (professor's words) | Design impact |
|---|---|---|---|
| | | | |
