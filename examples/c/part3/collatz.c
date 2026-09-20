/* Part 3 checkpoint input, written after the IR reader was frozen. while loop with continue and an early return. */

int collatz_steps(int n) {
    int steps = 0;
    if (n < 1)
        return -1;
    while (n != 1) {
        if (n % 2 == 0) {
            n = n / 2;
            steps++;
            continue;
        }
        n = 3 * n + 1;
        steps++;
    }
    return steps;
}
