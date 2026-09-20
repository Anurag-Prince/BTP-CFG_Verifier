/*
 * Regression program: sequence.c
 * Construct: sequential statements (no decisions). A call does NOT end a basic block.
 * Expected CFG: regression/expected/sequence.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int square(int x) {
    return x * x;
}

int sequence(int a, int b) {
    int sum = a + b;
    int diff = a - b;
    int result = square(sum) - square(diff);
    return result;
}

#ifdef REGRESSION_MAIN
/* Self-check, compiled only for the runtime test: exit code 0 means the program behaves as documented. */
int main(void) {
    return !(square(-3) == 9 && sequence(3, 2) == 24 && sequence(0, 5) == 0);
}
#endif
