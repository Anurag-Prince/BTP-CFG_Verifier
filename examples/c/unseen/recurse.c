/* Recursion, and mutual calls between the functions of one file. */
int fib(int n) {
    if (n < 2)
        return n;
    return fib(n - 1) + fib(n - 2);
}

int is_even(int n);

int is_odd(int n) {
    if (n == 0)
        return 0;
    return is_even(n - 1);
}

int is_even(int n) {
    if (n == 0)
        return 1;
    return is_odd(n - 1);
}
