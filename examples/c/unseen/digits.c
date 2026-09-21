/* Sequential loops, and a do-while with a continue in it. */
int digit_sum(int n) {
    int total = 0;
    if (n < 0)
        n = -n;
    do {
        total = total + n % 10;
        n = n / 10;
    } while (n > 0);
    return total;
}

int count_odd_digits(int n) {
    int odd = 0;
    if (n < 0)
        n = -n;
    do {
        int d = n % 10;
        n = n / 10;
        if (d % 2 == 0)
            continue;
        odd++;
    } while (n > 0);
    return odd;
}

int digital_root(int n) {
    while (n > 9)
        n = digit_sum(n);
    return n;
}
