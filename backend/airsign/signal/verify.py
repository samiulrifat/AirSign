"""Scoring a candidate against a template and turning it into a decision (sections 8.2 to 8.4)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .dtw import dtw_distance
from .enroll import Template, nearest_mean
from .pipeline import Processed
from .siamese import SiameseModel, cosine_distance

ACCEPT = "ACCEPT"
REJECT = "REJECT"
STEP_UP = "STEP_UP"

# Threshold multiplier k per risk tier (tier 3 is two signatures, each at tier 2).
TIER_K = {1: 2.0, 2: 1.0, 3: 1.0}
STEP_UP_MARGIN = 0.5
FUSION_W = 0.5


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


def score(t: Template, c: Processed, model: SiameseModel | None = None) -> Scores:
    dists = [dtw_distance(c.X, e) for e in t.X]
    s_dtw = nearest_mean(dists)
    z_dtw = (s_dtw - t.mu_dtw) / t.sigma_dtw

    s_nn = z_nn = None
    if model is not None and t.proto is not None and t.model_version == model.version:
        s_nn = cosine_distance(model.embed(c.X_fixed), t.proto)
        z_nn = (s_nn - t.mu_nn) / t.sigma_nn

    if z_nn is None:
        z, method = z_dtw, "dtw"
    else:
        z, method = FUSION_W * z_dtw + (1 - FUSION_W) * z_nn, "fused"
    return Scores(s_dtw, z_dtw, min(dists), s_nn, z_nn, z, method)


def decide(z: float, tier: int) -> str:
    """ACCEPT under the tier threshold, STEP_UP when only just under it, else REJECT."""
    k = TIER_K[tier]
    if z > k:
        return REJECT
    if z > k - STEP_UP_MARGIN:
        return STEP_UP
    return ACCEPT
