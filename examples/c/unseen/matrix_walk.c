/* Three-deep nesting with an early return out of the innermost loop. */
int find_in_grid(int grid[], int rows, int cols, int needle) {
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            if (grid[r * cols + c] == needle)
                return r * cols + c;
        }
    }
    return -1;
}

int count_above(int grid[], int n, int threshold) {
    int count = 0;
    for (int i = 0; i < n; i++) {
        if (grid[i] <= threshold)
            continue;
        count++;
    }
    return count;
}
