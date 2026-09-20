/*
 * Regression program: nested_if.c
 * Construct: if-else statements nested inside both arms of an outer if-else.
 * Expected CFG: regression/expected/nested_if.toml   Notes: docs/CONTROL_FLOW_FUNDAMENTALS.md
 * This is a development/regression case, NOT a limit on what the tool will accept.
 */

int quadrant(int x, int y) {
    int q;
    if (x >= 0) {
        if (y >= 0)
            q = 1;
        else
            q = 4;
    } else {
        if (y >= 0)
            q = 2;
        else
            q = 3;
    }
    return q;
}

#ifdef REGRESSION_MAIN
int main(void) {
    return !(quadrant(1, 1) == 1 && quadrant(-1, 1) == 2 && quadrant(-1, -1) == 3
             && quadrant(1, -1) == 4 && quadrant(0, 0) == 1);
}
#endif
