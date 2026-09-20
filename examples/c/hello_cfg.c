/*
 * Part 1 smoke-test program: small, deterministic, and covering a few control-flow shapes.
 *
 * The comments count each function's source-level decisions (one per condition; each operand of
 * && / || is a separate decision because of short-circuit evaluation). At -O0, each decision should
 * appear as exactly one two-way branch block in LLVM IR. tests/test_c_to_ir.py checks this.
 */
#include <stdio.h>

/* 3 decisions: (n < 0), loop test (i <= n), (i % 2 == 0).  Shapes: early return, for-loop, if inside loop. */
int sum_even_upto(int n) {
    if (n < 0)
        return 0;
    int sum = 0;
    for (int i = 0; i <= n; i++) {
        if (i % 2 == 0)
            sum += i;
    }
    return sum;
}

/* 3 decisions: (x > 10), (x < 100), (x < 0).  Shapes: short-circuit &&, else-if chain. */
int classify(int x) {
    int category = 0;
    if (x > 10 && x < 100)
        category = 1;
    else if (x < 0)
        category = -1;
    return category;
}

/* 2 decisions: (n < 0), do-while test (n != 0).  Shapes: if without else, do-while loop. */
int count_digits(int n) {
    int digits = 0;
    if (n < 0)
        n = -n;
    do {
        digits++;
        n /= 10;
    } while (n != 0);
    return digits;
}

/* 0 decisions: straight-line code. */
int main(void) {
    printf("sum_even_upto(10) = %d\n", sum_even_upto(10));
    printf("classify(50) = %d, classify(-5) = %d, classify(7) = %d\n", classify(50), classify(-5), classify(7));
    printf("count_digits(-12345) = %d\n", count_digits(-12345));
    return 0;
}
