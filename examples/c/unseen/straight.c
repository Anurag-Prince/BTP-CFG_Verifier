/* No control flow at all: one basic block per function. */
int mix(int a, int b, int c) {
    int t = a * 3 + b;
    int u = t - c * 2;
    return u + a;
}

int average3(int a, int b, int c) {
    return (a + b + c) / 3;
}
