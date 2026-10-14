"""Compact gesture fingerprints for near-exact replay detection (section 9.2).

A fingerprint is the time-normalized gesture averaged down to 6 x 32 and stored
as float16. It is enough to recognise a resubmitted recording, but far coarser
than the template itself. Distances are RMS differences, so the replay bound is
calibrated in fingerprint space and never mixed with DTW distances.
"""

from __future__ import annotations

import numpy as np

FP_LEN = 32


def fingerprint(X_fixed: np.ndarray) -> np.ndarray:
    c, n = X_fixed.shape
    block = n // FP_LEN
    fp = X_fixed[:, : block * FP_LEN].reshape(c, FP_LEN, block).mean(axis=2)
    return fp.reshape(-1).astype(np.float16)


def fp_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2)))
