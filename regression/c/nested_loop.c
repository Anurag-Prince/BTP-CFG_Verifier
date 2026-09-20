/*
 * Regression program: nested_loop.c
 * Construct: a for loop nested inside a for loop (two loops, two back edges, nesting depth 2).
 * Expected CFG: regression/expected/nested_loop.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

/* Number of pairs (i, j) with 0 <= j < i < n. */
int count_pairs(int n) {
    int count = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < i; j++) {
            count++;
        }
    }
    return count;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(count_pairs(4) == 6 && count_pairs(1) == 0 && count_pairs(0) == 0);
}
#endif
