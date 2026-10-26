"""Replay and injection checks (technical report, section 9.2 and 9.3).

Two kinds of result:
  replay flags     near-exact match with the template or an earlier accepted
                   attempt. A person never repeats a gesture exactly, so this locks
                   the payment intent.
  sensor flags     the stream does not look like it came from real phone hardware.
                   Each flag alone is weak, so they are counted.
"""

from __future__ import annotations

import numpy as np

from .enroll import Template
from .fingerprint import fingerprint, fp_distance
from .pipeline import Processed

MIN_JITTER_MS = 0.001
RATE_MISMATCH = 0.35  # relative difference between measured and declared interval
TOO_SMOOTH_LAG1 = 0.9995
TOO_WHITE_LAG1 = 0.2
# High-frequency residual variance (rad^2/s^2 and (m/s^2)^2). Generated streams sit
# near 1e-6 (resampling artifacts only), synthetic phone noise near 1e-4.
# TODO(phase 1): confirm against real recordings from every demo phone.
MIN_NOISE_VAR = 1e-5
# Noise in the quietest half second. On 60 synthetic people, generated streams
# measured at most 3e-8 and genuine recordings at least 2e-4; this sits in the gap.
MIN_QUIET_NOISE_VAR = 1e-6


def replay_flags(t: Template, c: Processed, min_dtw: float, prior_fps: list[np.ndarray]) -> list[str]:
    flags = []
    if min_dtw < t.replay_eps_dtw:
        flags.append("replay_of_enrollment")
    fp = fingerprint(c.X_fixed)
    if any(fp_distance(fp, e) < t.replay_eps_fp for e in t.fps):
        if "replay_of_enrollment" not in flags:
            flags.append("replay_of_enrollment")
    if any(fp_distance(fp, e) < t.replay_eps_fp for e in prior_fps):
        flags.append("replay_of_earlier_attempt")
    return flags


def sensor_flags(c: Processed, declared_hz: float | None) -> list[str]:
    flags = []
    pl = c.plausibility
    if c.sampling["std_dt_ms"] < MIN_JITTER_MS:
        flags.append("timestamps_too_regular")
    if declared_hz:
        expected = 1000.0 / declared_hz
        if abs(c.sampling["mean_dt_ms"] - expected) / expected > RATE_MISMATCH:
            flags.append("rate_mismatch")
    hf = np.array(pl.get("hf_var", []))
    quiet = np.array(pl.get("quiet_hf_var", []))
    if (quiet.size == 6 and quiet.max() < MIN_QUIET_NOISE_VAR) or (hf.size == 6 and np.all(hf < MIN_NOISE_VAR)):
        flags.append("no_sensor_noise")
    elif hf.size == 6 and np.ptp(hf[:3]) == 0 and np.ptp(hf[3:]) == 0:
        flags.append("identical_noise")
    lag1 = np.array(pl.get("lag1", []))
    if lag1.size == 6:
        if np.all(lag1 > TOO_SMOOTH_LAG1):
            flags.append("too_smooth")
        if np.all(lag1[:3] < TOO_WHITE_LAG1):
            flags.append("white_noise")
    if pl.get("coherence", 0.0) < -0.3:
        flags.append("channels_incoherent")
    return flags
