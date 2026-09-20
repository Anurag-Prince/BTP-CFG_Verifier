/*
 * Regression program: continue.c
 * Construct: continue (skip the rest of the current iteration).
 *   sum_odd              - in a for loop, continue jumps to the STEP block (i++).
 *   count_nonmultiples   - in a while loop, continue jumps straight back to the CONDITION block.
 * Expected CFG: regression/expected/continue.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

/* Sum of the odd numbers in [1, n]. */
int sum_odd(int n) {
    int sum = 0;
    for (int i = 1; i <= n; i++) {
        if (i % 2 == 0)
            continue;
        sum += i;
    }
    return sum;
}

/* How many of 1..n are NOT divisible by k. Precondition: k != 0. */
int count_nonmultiples(int n, int k) {
    int count = 0;
    int i = 0;
    while (i < n) {
        i++;
        if (i % k == 0)
            continue;
        count++;
    }
    return count;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(sum_odd(6) == 9 && sum_odd(0) == 0 && sum_odd(1) == 1
             && count_nonmultiples(10, 3) == 7 && count_nonmultiples(0, 3) == 0);
}
#endif
