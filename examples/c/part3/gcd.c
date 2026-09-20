/* Part 3 checkpoint input, written after the IR reader was frozen. Euclid's algorithm. */

int gcd(int a, int b) {
    if (a < 0)
        a = -a;
    if (b < 0)
        b = -b;
    while (b != 0) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

int lcm(int a, int b) {
    if (a == 0)
        return 0;
    if (b == 0)
        return 0;
    return a / gcd(a, b) * b;
}
