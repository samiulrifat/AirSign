"""Dynamic Time Warping with a Sakoe-Chiba band (technical report, section 8.1)."""

from __future__ import annotations

import math

import numpy as np

BAND_FRACTION = 0.15


def dtw_distance(X: np.ndarray, Y: np.ndarray, band_fraction: float = BAND_FRACTION) -> float:
    """Normalized DTW distance delta(X, Y) = D(N, M) / (N + M).

    X and Y are (channels, time). The local cost is the squared Euclidean distance
    across channels. The band keeps |i - j*N/M| <= r with r about 15 percent of N.
    """
    n, m = X.shape[1], Y.shape[1]
    # Local cost matrix, (n, m).
    cost = (
        np.sum(X**2, axis=0)[:, None]
        + np.sum(Y**2, axis=0)[None, :]
        - 2.0 * (X.T @ Y)
    )
    np.maximum(cost, 0.0, out=cost)
    r = max(2, math.ceil(band_fraction * max(n, m)))
    ratio = m / n

    rows = cost.tolist()
    inf = math.inf
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        centre = i * ratio
        lo = max(1, int(math.floor(centre - r * ratio)))
        hi = min(m, int(math.ceil(centre + r * ratio)))
        row = rows[i - 1]
        left = inf
        for j in range(lo, hi + 1):
            best = prev[j - 1]
            if prev[j] < best:
                best = prev[j]
            if left < best:
                best = left
            left = row[j - 1] + best
            cur[j] = left
        prev = cur
    total = prev[m]
    if total == inf:
        # The band could not reach the corner, which only happens for wildly
        # different lengths. Treat as maximally dissimilar.
        return inf
    return total / (n + m)
