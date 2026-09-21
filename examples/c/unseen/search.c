/* A loop with two exits, and a helper called from a condition. */
int is_sorted(int a[], int n) {
    for (int i = 1; i < n; i++) {
        if (a[i - 1] > a[i])
            return 0;
    }
    return 1;
}

int first_gap(int a[], int n) {
    if (!is_sorted(a, n))
        return -1;
    int i = 1;
    while (i < n) {
        if (a[i] - a[i - 1] > 1)
            break;
        i++;
    }
    if (i >= n)
        return -1;
    return a[i - 1] + 1;
}
