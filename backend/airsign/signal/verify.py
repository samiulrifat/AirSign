"""Scoring a candidate against a template and turning it into a decision (sections 8.2 to 8.4).

Thresholds, the sigma floor and the fusion method come from calibration.py, so
they can be tuned on labeled recordings without anyone enrolling again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import calibration
from .dtw import dtw_distance
from .enroll import Template, nearest_mean
from .pipeline import Processed
from .siamese import SiameseModel, cosine_distance

ACCEPT = "ACCEPT"
REJECT = "REJECT"
STEP_UP = "STEP_UP"


@dataclass
class Scores:
    s_dtw: float
    z_dtw: float
    min_dtw: float
    s_nn: float | None
    z_nn: float | None
    z: float  # the score the decision uses
    method: str

    def as_dict(self) -> dict:
        return asdict(self)


def score(t: Template, c: Processed, model: SiameseModel | None = None, cal: calibration.Calibration | None = None) -> Scores:
    cal = cal or calibration.get()
    dists = [dtw_distance(c.X, e) for e in t.X]
    s_dtw = nearest_mean(dists)
    z_dtw = calibration.zscore(s_dtw, t.mu_dtw, t.sd_dtw, cal)

    s_nn = z_nn = None
    if model is not None and t.proto is not None and t.model_version == model.version:
        s_nn = cosine_distance(model.embed(c.X_fixed), t.proto)
        z_nn = calibration.zscore(s_nn, t.mu_nn, t.sd_nn, cal)

    z, method = calibration.combine(z_dtw, z_nn, cal)
    return Scores(s_dtw, z_dtw, min(dists), s_nn, z_nn, z, method)


def tier_k(tier: int) -> float:
    return calibration.get().k(tier)


def decide(z: float, tier: int, cal: calibration.Calibration | None = None) -> str:
    """ACCEPT under the tier threshold, STEP_UP when only just under it, else REJECT."""
    cal = cal or calibration.get()
    k = cal.k(tier)
    if z > k:
        return REJECT
    if z > k - cal.step_up_margin:
        return STEP_UP
    return ACCEPT
