/*
 * Regression program: break.c
 * Construct: break (leave the innermost enclosing loop immediately).
 *   first_multiple  - break out of a for loop: the loop gets a second exit.
 *   halvings        - while (1) + break: the break is the ONLY way out of the loop.
 *   capped_pairs    - break inside an inner loop leaves only the inner loop.
 * Expected CFG: regression/expected/break.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

/* Smallest i in [1, n] divisible by k, or -1 if there is none. Precondition: k != 0. */
int first_multiple(int n, int k) {
    int found = -1;
    for (int i = 1; i <= n; i++) {
        if (i % k == 0) {
            found = i;
            break;
        }
    }
    return found;
}

/* How many times n can be halved (integer division) before it is <= 1. */
int halvings(int n) {
    int steps = 0;
    while (1) {
        if (n <= 1)
            break;
        n = n / 2;
        steps++;
    }
    return steps;
}

/* Number of pairs (i, j) with 0 <= j <= i < n, where the inner loop stops early via break. */
int capped_pairs(int n) {
    int count = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (j > i)
                break;
            count++;
        }
    }
    return count;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(first_multiple(10, 4) == 4 && first_multiple(3, 5) == -1
             && halvings(8) == 3 && halvings(1) == 0 && halvings(0) == 0
             && capped_pairs(3) == 6 && capped_pairs(0) == 0);
}
#endif
