/*
 * Regression program: for.c
 * Construct: for (init; condition; step) loop.
 * Expected CFG: regression/expected/for.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int factorial(int n) {
    int f = 1;
    for (int i = 2; i <= n; i++)
        f *= i;
    return f;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(factorial(5) == 120 && factorial(1) == 1 && factorial(0) == 1);
}
#endif
