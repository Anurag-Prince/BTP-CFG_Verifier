/* Part 3 checkpoint input, written after the IR reader was frozen.
 * A call inside a condition, early return inside a for loop, and do { ... } while (1) with break/continue. */

int is_prime(int n) {
    if (n < 2)
        return 0;
    for (int d = 2; d * d <= n; d++) {
        if (n % d == 0)
            return 0;
    }
    return 1;
}

int count_primes(int n) {
    int count = 0;
    int k = 2;
    do {
        if (k > n)
            break;
        if (is_prime(k) == 0) {
            k++;
            continue;
        }
        count++;
        k++;
    } while (1);
    return count;
}
