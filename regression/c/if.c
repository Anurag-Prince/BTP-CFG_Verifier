/*
 * Regression program: if.c
 * Construct: if without else; if whose then-branch returns early.
 * Expected CFG: regression/expected/if.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int clamp_negative(int x) {
    int y = x;
    if (x < 0)
        y = 0;
    return y;
}

int safe_div(int a, int b) {
    if (b == 0)
        return 0;
    return a / b;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(clamp_negative(-7) == 0 && clamp_negative(5) == 5 && clamp_negative(0) == 0
             && safe_div(7, 0) == 0 && safe_div(7, 2) == 3);
}
#endif
