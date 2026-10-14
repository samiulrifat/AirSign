"""Enrollment: quality gates, template and personal threshold (technical report, section 7).

Deviation from the report, on purpose: the report sets mu_enr and sigma_enr from
all enrollment pairs, but a candidate is scored as the mean of its m nearest
enrollment distances. Those statistics are not comparable (the nearest-m mean is
much smaller than the all-pairs mean), so the threshold would be far too
lenient. We instead score every enrollment repetition exactly like a candidate,
leave-one-out: the mean of its m nearest distances to the other repetitions.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np

from .dtw import dtw_distance
from .fingerprint import fingerprint, fp_distance
from .pipeline import Processed
from .siamese import SiameseModel, cosine_distance

MIN_REPS = 5
MAX_REPS = 10
M_NEAREST = 3
MIN_DIRECTION_CHANGES = 6
MIN_PEAK_ROTATION = 1.5  # rad/s, about 86 deg/s
MAX_DURATION_CV = 0.25
# sigma comes from only 5 to 10 leave-one-out scores, so it is often underestimated.
# The floor is a starting point from synthetic data, to be tuned on real recordings.
SIGMA_FLOOR_FRACTION = 0.5
REPLAY_FRACTION = 0.25


class EnrollmentError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def check_sample(p: Processed) -> None:
    """Per-repetition quality gate. Raises EnrollmentError with a reason people can act on."""
    if len([c for c in p.flat_channels if c < 3]) >= 2:
        raise EnrollmentError("too_flat", "The phone barely rotated. Use a gesture that turns the phone.")
    if p.peak_rotation < MIN_PEAK_ROTATION:
        raise EnrollmentError("too_weak", "The gesture is too gentle. Move with a bit more energy.")
    if p.direction_changes < MIN_DIRECTION_CHANGES:
        raise EnrollmentError(
            "too_simple",
            "The gesture is too simple and would be easy to copy. Try drawing your initials or a shape.",
        )


def nearest_mean(dists: list[float], m: int = M_NEAREST) -> float:
    k = min(m, len(dists))
    return float(np.mean(sorted(dists)[:k]))


@dataclass
class Template:
    X: list[np.ndarray]  # preprocessed repetitions, each 6 x N_i
    fps: np.ndarray  # (n_submitted, 192) float16 fingerprints of all submitted repetitions
    mu_dtw: float
    sigma_dtw: float
    replay_eps_dtw: float
    replay_eps_fp: float
    emb: np.ndarray | None = None  # (n, 128)
    proto: np.ndarray | None = None  # (128,)
    mu_nn: float | None = None
    sigma_nn: float | None = None
    model_version: str | None = None

    @property
    def n(self) -> int:
        return len(self.X)

    def to_bytes(self) -> bytes:
        arrays = {f"X{i}": x.astype(np.float32) for i, x in enumerate(self.X)}
        arrays["fps"] = self.fps
        arrays["scalars"] = np.array([self.mu_dtw, self.sigma_dtw, self.replay_eps_dtw, self.replay_eps_fp])
        if self.emb is not None:
            arrays["emb"] = self.emb.astype(np.float32)
            arrays["proto"] = self.proto.astype(np.float32)
            arrays["nn"] = np.array([self.mu_nn, self.sigma_nn])
            arrays["model_version"] = np.array(self.model_version or "")
        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Template":
        with np.load(io.BytesIO(data)) as z:
            n = len([k for k in z.files if k.startswith("X")])
            X = [z[f"X{i}"].astype(np.float64) for i in range(n)]
            mu, sigma, eps_dtw, eps_fp = z["scalars"].tolist()
            t = cls(X=X, fps=z["fps"], mu_dtw=mu, sigma_dtw=sigma, replay_eps_dtw=eps_dtw, replay_eps_fp=eps_fp)
            if "emb" in z.files:
                t.emb = z["emb"].astype(np.float64)
                t.proto = z["proto"].astype(np.float64)
                t.mu_nn, t.sigma_nn = z["nn"].tolist()
                t.model_version = str(z["model_version"])
        return t


MAX_DROPPED = 2
OUTLIER_MIN_RATIO = 1.5


def _robust_outliers(values: np.ndarray) -> np.ndarray:
    """Boolean mask of repetitions to drop: above median + 3 MAD, as in the report.

    With only 5 to 10 values the MAD can be tiny, which would drop repetitions
    that are barely above the median. So a repetition must also be at least 1.5
    times the median, and at most the two worst are dropped.
    """
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    mask = (values > med + 3.0 * mad) & (values > OUTLIER_MIN_RATIO * med)
    if mask.sum() > MAX_DROPPED:
        worst = np.argsort(values)[::-1][:MAX_DROPPED]
        mask = np.zeros(len(values), dtype=bool)
        mask[worst] = True
    return mask


def _mu_sigma(scores: list[float]) -> tuple[float, float]:
    mu = float(np.mean(scores))
    sigma = float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0
    return mu, max(sigma, SIGMA_FLOOR_FRACTION * mu, 1e-6)


def build_template(samples: list[Processed], model: SiameseModel | None = None) -> tuple[Template, dict]:
    """Consistency gates, then the template. Returns (template, report)."""
    if len(samples) < MIN_REPS:
        raise EnrollmentError("not_enough", f"At least {MIN_REPS} good repetitions are needed.")
    samples = samples[:MAX_REPS]
    n = len(samples)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            D[i, j] = D[j, i] = dtw_distance(samples[i].X, samples[j].X)

    avg = D.sum(axis=1) / (n - 1)
    drop = _robust_outliers(avg)
    keep = [i for i in range(n) if not drop[i]]
    report = {"submitted": n, "dropped_inconsistent": int(drop.sum())}
    if len(keep) < MIN_REPS:
        raise EnrollmentError(
            "inconsistent",
            "Some repetitions were too different from the others. Record a few more, the same way each time.",
        )

    durations = np.array([samples[i].duration_s for i in keep])
    cv = float(durations.std() / durations.mean())
    report["duration_cv"] = cv
    if cv > MAX_DURATION_CV:
        raise EnrollmentError("uneven_speed", "Your repetitions vary a lot in speed. Try a steady rhythm.")

    Dk = D[np.ix_(keep, keep)]
    k = len(keep)
    loo = [nearest_mean([Dk[i, j] for j in range(k) if j != i]) for i in range(k)]
    mu, sigma = _mu_sigma(loo)
    pair_min = float(min(Dk[i, j] for i in range(k) for j in range(i + 1, k)))

    # Fingerprints of every submitted repetition, including dropped ones, so that
    # replaying any enrollment recording is caught.
    fps = np.stack([fingerprint(s.X_fixed) for s in samples])
    kfps = fps[keep]
    fp_pair_min = min(fp_distance(kfps[i], kfps[j]) for i in range(k) for j in range(i + 1, k))

    t = Template(
        X=[samples[i].X for i in keep],
        fps=fps,
        mu_dtw=mu,
        sigma_dtw=sigma,
        replay_eps_dtw=REPLAY_FRACTION * pair_min,
        replay_eps_fp=REPLAY_FRACTION * fp_pair_min,
    )

    if model is not None:
        emb = np.stack([model.embed(samples[i].X_fixed) for i in keep])
        loo_nn = []
        for i in range(k):
            others = np.delete(emb, i, axis=0).mean(axis=0)
            loo_nn.append(cosine_distance(emb[i], others))
        proto = emb.mean(axis=0)
        t.emb = emb
        t.proto = proto / np.linalg.norm(proto)
        t.mu_nn, t.sigma_nn = _mu_sigma(loo_nn)
        t.model_version = model.version

    report.update({"kept": k, "mu_dtw": mu, "sigma_dtw": sigma})
    return t, report
