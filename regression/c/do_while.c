/*
 * Regression program: do_while.c
 * Construct: do-while loop (body runs first; condition tested AFTER each iteration, so at least one iteration).
 * Expected CFG: regression/expected/do_while.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int count_digits(int n) {
    int digits = 0;
    do {
        digits++;
        n /= 10;
    } while (n != 0);
    return digits;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(count_digits(0) == 1 && count_digits(7) == 1 && count_digits(12345) == 5
             && count_digits(-120) == 3);
}
#endif
