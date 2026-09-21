# Unseen programs (Part 8 evaluation)

These programs were written **after** the CFG generator, the normalizer and the matcher were finished and
frozen. Nothing in `src/cfgverify/` was changed to make any of them work — `scripts/checkpoint_part8.sh`
proves that by taking the checksums of those modules before and after running this whole directory.

They are deliberately not like the regression set: deeper nesting, several loops in sequence, early returns
from inside nested loops, `continue` in a `do`-`while`, loops with two exits, straight-line code with no
control flow at all, recursion, and calls between the functions of one file.

The ten regression programs in `regression/c/` are **regression tests**, not the supported input set.
