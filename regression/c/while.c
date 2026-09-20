/*
 * Regression program: while.c
 * Construct: while loop (condition tested BEFORE each iteration; the body may run zero times).
 * Expected CFG: regression/expected/while.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int sum_to(int n) {
    int sum = 0;
    int i = 1;
    while (i <= n) {
        sum += i;
        i++;
    }
    return sum;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(sum_to(4) == 10 && sum_to(1) == 1 && sum_to(0) == 0 && sum_to(-3) == 0);
}
#endif
