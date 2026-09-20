/* Part 3 checkpoint input, written after the IR reader was frozen. Arrays, else-if chain, early return in a loop. */

int binary_search(const int *arr, int n, int key) {
    int lo = 0;
    int hi = n - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;
        if (arr[mid] == key)
            return mid;
        else if (arr[mid] < key)
            lo = mid + 1;
        else
            hi = mid - 1;
    }
    return -1;
}
