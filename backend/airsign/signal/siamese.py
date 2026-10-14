"""NumPy inference for the Siamese 1D CNN (technical report, section 8.3).

The network is trained offline with PyTorch (see ml/). The server only needs the
forward pass, which for about 53 thousand parameters is a few milliseconds in
NumPy, so the deployed app does not carry a PyTorch dependency.

If no weights file exists the neural scorer is simply unavailable and the
verifier falls back to DTW alone.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "models" / "siamese.npz"
BN_EPS = 1e-5


def _conv1d_same(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """x (cin, n), w (cout, cin, k) -> (cout, n) with zero 'same' padding."""
    cout, cin, k = w.shape
    pad = k // 2
    xp = np.pad(x, ((0, 0), (pad, k - 1 - pad)))
    n = x.shape[1]
    # (cin*k, n) column matrix
    cols = np.stack([xp[:, i : i + n] for i in range(k)], axis=1).reshape(cin * k, n)
    return w.reshape(cout, cin * k) @ cols + b[:, None]


def _bn(x: np.ndarray, p: dict, name: str) -> np.ndarray:
    scale = p[f"{name}.weight"] / np.sqrt(p[f"{name}.running_var"] + BN_EPS)
    return (x - p[f"{name}.running_mean"][:, None]) * scale[:, None] + p[f"{name}.bias"][:, None]


def _maxpool2(x: np.ndarray) -> np.ndarray:
    n = x.shape[1] // 2 * 2
    return x[:, :n].reshape(x.shape[0], -1, 2).max(axis=2)


class SiameseModel:
    def __init__(self, params: dict[str, np.ndarray], version: str):
        self.p = params
        self.version = version

    def embed(self, X_fixed: np.ndarray) -> np.ndarray:
        p = self.p
        h = np.maximum(_bn(_conv1d_same(X_fixed, p["conv1.weight"], p["conv1.bias"]), p, "bn1"), 0)
        h = _maxpool2(h)
        h = np.maximum(_bn(_conv1d_same(h, p["conv2.weight"], p["conv2.bias"]), p, "bn2"), 0)
        h = _maxpool2(h)
        h = np.maximum(_bn(_conv1d_same(h, p["conv3.weight"], p["conv3.bias"]), p, "bn3"), 0)
        h = h.mean(axis=1)
        e = p["fc.weight"] @ h + p["fc.bias"]
        return e / max(np.linalg.norm(e), 1e-12)


@lru_cache(maxsize=1)
def get_model() -> SiameseModel | None:
    if not WEIGHTS_PATH.exists():
        return None
    with np.load(WEIGHTS_PATH) as data:
        params = {k: data[k].astype(np.float64) for k in data.files if k != "version"}
        version = str(data["version"]) if "version" in data.files else "unknown"
    return SiameseModel(params, version)


def cosine_distance(u: np.ndarray, v: np.ndarray) -> float:
    return float(1.0 - np.dot(u, v) / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12))
