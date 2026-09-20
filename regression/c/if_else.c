/*
 * Regression program: if_else.c
 * Construct: if-else (two arms that join); else-if chain.
 * Expected CFG: regression/expected/if_else.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int max2(int a, int b) {
    int m;
    if (a > b)
        m = a;
    else
        m = b;
    return m;
}

int sign(int x) {
    int s;
    if (x > 0)
        s = 1;
    else if (x < 0)
        s = -1;
    else
        s = 0;
    return s;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(max2(3, 9) == 9 && max2(9, 3) == 9 && max2(4, 4) == 4
             && sign(12) == 1 && sign(-12) == -1 && sign(0) == 0);
}
#endif
