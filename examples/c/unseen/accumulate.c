/* Nested loop where the inner loop's bound depends on the outer index, plus a break in the outer loop. */
int triangular_sum(int n) {
    int total = 0;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j <= i; j++) {
            total = total + j;
        }
        if (total > 1000)
            break;
    }
    return total;
}

int power_mod(int base, int exponent, int modulus) {
    int result = 1;
    while (exponent > 0) {
        if (exponent % 2 == 1)
            result = (result * base) % modulus;
        base = (base * base) % modulus;
        exponent = exponent / 2;
    }
    return result;
}

/* Three-deep loop nesting with a conditional in the innermost body. */
int count_triples(int n, int limit) {
    int found = 0;
    for (int a = 1; a < n; a++) {
        for (int b = a; b < n; b++) {
            for (int c = b; c < n; c++) {
                if (a * a + b * b == c * c)
                    found++;
                if (found > limit)
                    return found;
            }
        }
    }
    return found;
}
