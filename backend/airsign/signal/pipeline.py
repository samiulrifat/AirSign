"""Deterministic preprocessing for motion recordings (technical report, section 6).

Every recording, for enrollment and verification alike, goes through `preprocess`
so that scores are comparable across phones.

Input rows are [t_ms, ax, ay, az, gx, gy, gz] as reported by the browser
DeviceMotion event: acceleration in m/s^2, rotation rate in degrees per second.
Output channel order is [gx, gy, gz, ax, ay, az] with gyro converted to rad/s.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, sosfiltfilt

FS = 50.0  # resampling rate, Hz
DT = 1.0 / FS
LOWPASS_HZ = 10.0
GRAVITY_HP_HZ = 0.3
FIXED_LEN = 128

MIN_ROWS = 30
MAX_ROWS = 1500
MIN_RECORDING_S = 1.0
MAX_RECORDING_S = 7.0  # 5 s gesture plus a still lead-in and release
MIN_SEGMENT_S = 1.0
MAX_SEGMENT_S = 5.0
MAX_ACCEL = 200.0  # m/s^2, far above any hand motion
MAX_GYRO_DPS = 2000.0  # typical phone gyro full scale

NOISE_WINDOW_S = 0.5
SMOOTH_S = 0.2
START_HOLD_S = 0.1
END_HOLD_S = 0.3
ACTIVITY_LAMBDA = 0.25  # weight of linear acceleration energy vs rotation energy
MIN_ACTIVITY_MARGIN = 0.05  # absolute floor on the start threshold above the noise mean

_LOWPASS_SOS = butter(4, LOWPASS_HZ, btype="low", fs=FS, output="sos")
_GRAVITY_SOS = butter(2, GRAVITY_HP_HZ, btype="high", fs=FS, output="sos")


class RecordingError(ValueError):
    """The recording cannot be used. `code` is stable, `message` is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Processed:
    X: np.ndarray  # 6 x N, standardized active segment, for DTW
    X_fixed: np.ndarray  # 6 x 128, time-normalized X, for the neural model
    duration_s: float
    peak_rotation: float  # rad/s, before normalization
    direction_changes: int
    energy: float
    flat_channels: list[int]
    sampling: dict  # mean_dt_ms, std_dt_ms, n_rows
    plausibility: dict = field(default_factory=dict)  # raw features for the replay check


def validate(samples) -> np.ndarray:
    """Stage 0. Returns an (n, 7) float array or raises RecordingError."""
    try:
        arr = np.asarray(samples, dtype=float)
    except (TypeError, ValueError):
        raise RecordingError("bad_format", "Samples must be rows of 7 numbers.")
    if arr.ndim != 2 or arr.shape[1] != 7:
        raise RecordingError("bad_format", "Samples must be rows of 7 numbers.")
    n = arr.shape[0]
    if n < MIN_ROWS:
        raise RecordingError("too_short", "The recording is too short. Hold the button for the whole gesture.")
    if n > MAX_ROWS:
        raise RecordingError("too_long", "The recording has too many samples.")
    if not np.all(np.isfinite(arr)):
        raise RecordingError("bad_values", "The recording contains missing values.")
    t = arr[:, 0]
    if np.any(np.diff(t) <= 0):
        raise RecordingError("bad_timestamps", "Timestamps must increase.")
    duration = (t[-1] - t[0]) / 1000.0
    if duration < MIN_RECORDING_S:
        raise RecordingError("too_short", "The recording is shorter than one second.")
    if duration > MAX_RECORDING_S:
        raise RecordingError("too_long", "The recording is longer than the allowed time.")
    if np.any(np.abs(arr[:, 1:4]) > MAX_ACCEL) or np.any(np.abs(arr[:, 4:7]) > MAX_GYRO_DPS):
        raise RecordingError("out_of_range", "Sensor values are outside the physical range of a phone.")
    mean_dt = float(np.mean(np.diff(t)))
    if not (4.0 <= mean_dt <= 50.0):
        raise RecordingError("bad_rate", "The sensor sampling rate is outside 20 to 250 Hz.")
    return arr


def _moving_average(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return x
    kernel = np.ones(width) / width
    return np.convolve(x, kernel, mode="same")


def _lag1_autocorr(x: np.ndarray) -> float:
    x = x - x.mean()
    denom = float(np.dot(x, x))
    if denom < 1e-12:
        return 1.0
    return float(np.dot(x[:-1], x[1:]) / denom)


def _quietest_window_var(residual: np.ndarray, window_s: float = NOISE_WINDOW_S) -> np.ndarray:
    """Per-channel variance of the residual in the window whose loudest channel is quietest."""
    w = max(5, int(round(window_s * FS)))
    n = residual.shape[1]
    if n <= w:
        return residual.var(axis=1)
    best = None
    for i in range(0, n - w + 1, 5):
        v = residual[:, i : i + w].var(axis=1)
        if best is None or v.max() < best.max():
            best = v
    return best


def segment(activity: np.ndarray) -> tuple[int, int, dict]:
    """Stage 4. Find the active part with hysteresis thresholds from the noise floor.

    The report takes the noise floor from the first 0.5 s. If the user started
    moving immediately that window is not still, so we use the quieter of the
    first window and the quietest window anywhere in the recording.
    """
    n = len(activity)
    w = max(3, int(round(NOISE_WINDOW_S * FS)))
    smooth = _moving_average(activity, max(1, int(round(SMOOTH_S * FS))))

    first = smooth[:w]
    if n > w:
        means = np.convolve(smooth, np.ones(w) / w, mode="valid")
        q = int(np.argmin(means))
        quiet = smooth[q : q + w]
    else:
        quiet = first
    window = first if first.mean() <= quiet.mean() else quiet
    mu, sigma = float(window.mean()), float(window.std())
    lead_in_still = bool(first.mean() <= max(quiet.mean() * 3.0, mu + MIN_ACTIVITY_MARGIN))

    theta_high = max(mu + 4.0 * sigma, mu + MIN_ACTIVITY_MARGIN)
    theta_low = max(mu + 2.0 * sigma, mu + 0.5 * MIN_ACTIVITY_MARGIN)
    start_hold = max(1, int(round(START_HOLD_S * FS)))
    end_hold = max(1, int(round(END_HOLD_S * FS)))

    above = smooth > theta_high
    start = None
    run = 0
    for i, a in enumerate(above):
        run = run + 1 if a else 0
        if run >= start_hold:
            start = i - start_hold + 1
            break
    if start is None:
        raise RecordingError("no_motion", "No gesture was detected. Move the phone more clearly.")

    # Trim only the trailing still part. A pause inside the gesture (between two
    # letters, say) must not end it, so we search backwards from the release.
    active = np.nonzero(smooth[start:] >= theta_low)[0]
    end = start + int(active[-1]) + 1 if active.size else n
    end = min(n, end + end_hold // 3)  # keep the tail of the last stroke
    info = {"noise_mu": mu, "noise_sigma": sigma, "lead_in_still": lead_in_still}
    return start, end, info


def _count_direction_changes(gyro: np.ndarray, deadband: float = 0.3) -> int:
    """Sign changes summed over gyro channels, ignoring wiggles inside a deadband (rad/s)."""
    total = 0
    for ch in gyro:
        sign = 0
        for v in ch:
            if v > deadband:
                s = 1
            elif v < -deadband:
                s = -1
            else:
                continue
            if sign != 0 and s != sign:
                total += 1
            sign = s
    return total


def preprocess(samples, accel_includes_gravity: bool = False) -> Processed:
    arr = validate(samples)
    t_ms = arr[:, 0]
    dts = np.diff(t_ms)
    sampling = {
        "mean_dt_ms": float(dts.mean()),
        "std_dt_ms": float(dts.std()),
        "n_rows": int(arr.shape[0]),
    }

    # Stage 2: resample to a uniform 50 Hz grid. Gyro deg/s -> rad/s.
    t = (t_ms - t_ms[0]) / 1000.0
    grid = np.arange(0.0, t[-1] + 1e-9, DT)
    raw = np.empty((6, len(grid)))
    cols = [4, 5, 6, 1, 2, 3]  # gx gy gz ax ay az
    for out_ch, col in enumerate(cols):
        raw[out_ch] = np.interp(grid, t, arr[:, col])
    raw[:3] *= np.pi / 180.0

    # Stage 1: gravity. Done after resampling because the filter needs a uniform grid.
    if accel_includes_gravity:
        raw[3:] = sosfiltfilt(_GRAVITY_SOS, raw[3:], axis=1)

    # Stage 3: zero-phase low-pass.
    filt = sosfiltfilt(_LOWPASS_SOS, raw, axis=1)

    # Stage 4: segmentation on rotation and acceleration energy.
    activity = np.sum(filt[:3] ** 2, axis=0) + ACTIVITY_LAMBDA * np.sum(filt[3:] ** 2, axis=0)
    start, end, seg_info = segment(activity)
    seg = filt[:, start:end]
    duration = seg.shape[1] / FS
    if duration < MIN_SEGMENT_S:
        raise RecordingError("gesture_too_short", "The gesture is shorter than one second. Make it a bit longer.")
    if duration > MAX_SEGMENT_S:
        raise RecordingError("gesture_too_long", "The gesture is longer than five seconds. Make it shorter.")

    peak_rotation = float(np.max(np.linalg.norm(seg[:3], axis=0)))
    energy = float(np.mean(activity[start:end]))
    direction_changes = _count_direction_changes(seg[:3])

    # Stage 5: per-channel standardization inside the segment.
    X = np.zeros_like(seg)
    flat = []
    floors = [0.05] * 3 + [0.05] * 3  # rad/s and m/s^2
    for c in range(6):
        sd = seg[c].std()
        if sd < floors[c]:
            flat.append(c)
            continue
        X[c] = (seg[c] - seg[c].mean()) / sd

    # Stage 6: fixed-length copy for the neural model.
    src = np.linspace(0.0, 1.0, X.shape[1])
    dst = np.linspace(0.0, 1.0, FIXED_LEN)
    X_fixed = np.vstack([np.interp(dst, src, X[c]) for c in range(6)])

    # Features for the sensor plausibility check (section 9.3).
    lead = raw[:, : max(0, min(start, int(NOISE_WINDOW_S * FS)))]
    noise_var = lead.var(axis=1).tolist() if lead.shape[1] >= 5 else None
    gyro_env = _moving_average(np.linalg.norm(raw[:3, start:end], axis=0), 5)
    acc_env = _moving_average(np.linalg.norm(raw[3:, start:end], axis=0), 5)
    if gyro_env.std() > 1e-9 and acc_env.std() > 1e-9:
        coherence = float(np.corrcoef(gyro_env, acc_env)[0, 1])
    else:
        coherence = 0.0
    plausibility = {
        "noise_var": noise_var,
        # Sensor noise above the 10 Hz cut-off. Real hardware always has some;
        # mathematically generated streams have almost none.
        "hf_var": (raw - filt).var(axis=1).tolist(),
        # The same residual in the quietest half second, per channel. Movement can
        # leak into the whole-recording figure; a still phone shows only sensor noise.
        "quiet_hf_var": _quietest_window_var(raw - filt).tolist(),
        "lag1": [_lag1_autocorr(raw[c, start:end]) for c in range(6)],
        "coherence": coherence,
        "lead_in_still": seg_info["lead_in_still"],
        "lead_in_s": start / FS,
    }

    return Processed(
        X=X,
        X_fixed=X_fixed,
        duration_s=duration,
        peak_rotation=peak_rotation,
        direction_changes=direction_changes,
        energy=energy,
        flat_channels=flat,
        sampling=sampling,
        plausibility=plausibility,
    )
