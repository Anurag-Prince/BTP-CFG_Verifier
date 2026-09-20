# Regression programs

These are small C programs, one per basic control-flow construct, used as **development and regression cases**.
They are **not** a limit on the inputs the tool will accept.

| Program | Construct | Functions |
|---|---|---|
| `sequence.c` | sequential statements (a call does not end a block) | `square`, `sequence` |
| `if.c` | `if` without `else`; early `return` | `clamp_negative`, `safe_div` |
| `if_else.c` | `if-else`; `else if` chain | `max2`, `sign` |
| `nested_if.c` | `if-else` nested in both arms | `quadrant` |
| `while.c` | `while` | `sum_to` |
| `for.c` | `for` | `factorial` |
| `do_while.c` | `do-while` | `count_digits` |
| `nested_loop.c` | `for` inside `for` | `count_pairs` |
| `break.c` | `break` in `for`, `while (1)` and an inner loop | `first_multiple`, `halvings`, `capped_pairs` |
| `continue.c` | `continue` in `for` and in `while` | `sum_odd`, `count_nonmultiples` |

## Conventions
- **No `#include`s and only `int` functions.** Each file therefore compiles for any target triple without a C library,
  which matters because the target ISA is still TO CONFIRM WITH PROFESSOR ([A1]).
- **Self-check.** Each file has a `main` inside `#ifdef REGRESSION_MAIN` that returns 0 when every documented
  result holds. The tests build it with `-DREGRESSION_MAIN`. CFG extraction builds the file without it, so `main` is
  not part of the verified code.
- **Expected CFG.** `expected/<name>.toml` lists every function's blocks, T/F/U edges, conditions (C and IR),
  back edges and loops. The format is described in `src/cfgverify/expected.py`.

## Checking
```bash
pytest tests/test_regression_cfgs.py   # expected CFG vs LLVM's CFG, plus the other checks
bash scripts/check_part2.sh            # the tests, plus LLVM's CFG drawings in build/regression/llvm_cfg/
```
The explanation of every construct and its expected CFG diagram is in
[`docs/CONTROL_FLOW_FUNDAMENTALS.md`](../docs/CONTROL_FLOW_FUNDAMENTALS.md).
