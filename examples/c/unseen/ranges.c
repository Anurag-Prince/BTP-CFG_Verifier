/* Chained else-if, and a loop whose body is a chain of conditions. */
int classify(int x) {
    if (x < -100)
        return -2;
    else if (x < 0)
        return -1;
    else if (x == 0)
        return 0;
    else if (x < 100)
        return 1;
    else
        return 2;
}

int histogram(int a[], int n, int bucket) {
    int count = 0;
    for (int i = 0; i < n; i++) {
        int k = classify(a[i]);
        if (k == bucket)
            count++;
    }
    return count;
}
