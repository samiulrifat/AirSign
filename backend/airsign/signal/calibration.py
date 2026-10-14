"""Decision settings for the verifier, loaded from backend/models/thresholds.json.

The file is written by scripts/calibrate.py from labeled recordings. Without it
the defaults from the technical report apply. Keeping these out of the template
means thresholds can be retuned without anyone enrolling again.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "models" / "thresholds.json"
METHODS = ("dtw", "nn", "fused", "and")
log = logging.getLogger("airsign.calibration")


@dataclass
class Calibration:
    # z threshold per risk tier; tier 3 is two signatures, each at the tier 3 value
    tier_k: dict[int, float] = field(default_factory=lambda: {1: 2.0, 2: 1.0, 3: 1.0})
    step_up_margin: float = 0.5
    # sigma comes from only 5 to 10 leave-one-out scores, so it is often too small
    sigma_floor_fraction: float = 0.5
    # Absolute minimum for the DTW sigma. Channels are standardized, so DTW distances
    # share one scale across people; this stops an unusually consistent enrollment
    # from making the threshold impossibly tight. Not applied to neural distances.
    sigma_floor_abs: float = 0.3
    # which score decides when the neural model is available: dtw, nn, fused or and
    method: str = "fused"
    fusion_w: float = 0.5
    source: str = "defaults from the technical report"

    def k(self, tier: int) -> float:
        return self.tier_k[tier]

    def to_json(self) -> str:
        d = asdict(self)
        d["tier_k"] = {str(k): v for k, v in self.tier_k.items()}
        return json.dumps(d, indent=2)


def load(path: Path = PATH) -> Calibration:
    if not path.exists():
        return Calibration()
    try:
        d = json.loads(path.read_text())
        d["tier_k"] = {int(k): float(v) for k, v in d["tier_k"].items()}
        c = Calibration(**d)
        if c.method not in METHODS or set(c.tier_k) != {1, 2, 3}:
            raise ValueError("bad method or tiers")
        return c
    except (ValueError, KeyError, TypeError) as e:
        log.error("ignoring %s: %s", path, e)
        return Calibration()


@lru_cache(maxsize=1)
def get() -> Calibration:
    return load()


def combine(z_dtw: float, z_nn: float | None, cal: Calibration) -> tuple[float, str]:
    """The decision score for the configured method. DTW alone when there is no neural score."""
    if z_nn is None:
        return z_dtw, "dtw"
    if cal.method == "dtw":
        return z_dtw, "dtw"
    if cal.method == "nn":
        return z_nn, "nn"
    if cal.method == "and":
        return max(z_dtw, z_nn), "and"
    return cal.fusion_w * z_dtw + (1 - cal.fusion_w) * z_nn, "fused"


def zscore(s: float, mu: float, sd: float, cal: Calibration, abs_floor: float = 0.0) -> float:
    return (s - mu) / max(sd, cal.sigma_floor_fraction * mu, abs_floor, 1e-6)
