"""Synthetic motion signatures for simulation mode and tests.

A persona (any string, usually a user id) maps to a stable base gesture. From it
we generate:
  genuine    the same person repeating the gesture: small timing, strength and
             grip changes, as in the augmentation of section 8.3
  imitation  someone who watched the gesture and copies its shape, but not its
             exact rhythm, strength or rotation
  impostor   a different person's gesture
Each recording has a still lead-in, sensor noise and timestamp jitter, and is
returned in the same row format the phone sends: [t_ms, ax, ay, az, gx, gy, gz]
with gyro in degrees per second.

Simulated data is labeled as such everywhere it is used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np


def _seed(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "little")


def _rotation(rng: np.random.Generator, max_deg: float) -> np.ndarray:
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    angle = np.deg2rad(rng.uniform(-max_deg, max_deg))
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + np.sin(angle) * K + (1 - np.cos(angle)) * (K @ K)


@dataclass
class Gesture:
    """Sum of two-lobed bumps on random axes. Times in seconds, rad/s and m/s^2."""

    centers: np.ndarray  # (k,)
    widths: np.ndarray  # (k,)
    amps: np.ndarray  # (k,)
    axes: np.ndarray  # (k, 3) unit vectors
    lever: np.ndarray  # (3, 3) couples rotation to linear acceleration
    acc_axes: np.ndarray  # (k, 3)
    acc_amps: np.ndarray  # (k,)
    duration: float

    def gyro(self, t: np.ndarray) -> np.ndarray:
        x = (t[None, :] - self.centers[:, None]) / self.widths[:, None]
        lobes = 1.65 * x * np.exp(-0.5 * x**2)  # derivative-of-Gaussian, peak about 1
        return (self.amps[:, None] * lobes).T @ self.axes  # (n, 3)

    def accel(self, t: np.ndarray) -> np.ndarray:
        coupled = self.gyro(t - 0.04) @ self.lever.T
        x = (t[None, :] - self.centers[:, None]) / (1.3 * self.widths[:, None])
        own = (self.acc_amps[:, None] * np.exp(-0.5 * x**2)).T @ self.acc_axes
        return coupled + own


def _sign_changes(g: Gesture, deadband: float = 0.5) -> int:
    t = np.arange(0.0, g.duration, 0.02)
    total = 0
    for ch in g.gyro(t).T:
        s = np.sign(ch[np.abs(ch) > deadband])
        total += int(np.sum(s[1:] != s[:-1]))
    return total


def base_gesture(persona: str) -> Gesture:
    """A stable gesture for this persona, regenerated until it is complex enough to enroll."""
    for attempt in range(20):
        g = _base_gesture(persona, attempt)
        if _sign_changes(g) >= 10:
            return g
    return g


def _base_gesture(persona: str, attempt: int) -> Gesture:
    rng = np.random.default_rng(_seed("base", persona, str(attempt)))
    k = int(rng.integers(5, 8))
    duration = float(rng.uniform(2.0, 3.4))
    centers = np.sort(rng.uniform(0.2, duration - 0.2, size=k))
    widths = rng.uniform(0.08, 0.22, size=k)
    amps = rng.uniform(1.8, 4.5, size=k) * rng.choice([-1.0, 1.0], size=k)
    axes = rng.normal(size=(k, 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    lever = rng.normal(scale=0.6, size=(3, 3))
    acc_axes = rng.normal(size=(k, 3))
    acc_axes /= np.linalg.norm(acc_axes, axis=1, keepdims=True)
    acc_amps = rng.uniform(0.5, 2.5, size=k) * rng.choice([-1.0, 1.0], size=k)
    return Gesture(centers, widths, amps, axes, lever, acc_axes, acc_amps, duration)


def imitate(g: Gesture, rng: np.random.Generator) -> Gesture:
    """What an observer reproduces: the overall shape, with their own rhythm and grip."""
    k = len(g.centers)
    speed = rng.uniform(0.8, 1.25)
    jitter = rng.normal(scale=0.06, size=k)
    centers = np.sort(g.centers * speed + jitter)
    centers = np.clip(centers, 0.15, None)
    R = _rotation(rng, 20.0)
    return Gesture(
        centers=centers,
        widths=g.widths * rng.uniform(0.7, 1.4, size=k),
        amps=g.amps * rng.uniform(0.7, 1.3, size=k),
        axes=_normalize(g.axes @ R.T + rng.normal(scale=0.25, size=(k, 3))),
        lever=rng.normal(scale=0.6, size=(3, 3)),  # different arm and grip
        acc_axes=_normalize(g.acc_axes + rng.normal(scale=0.5, size=(k, 3))),
        acc_amps=g.acc_amps * rng.uniform(0.5, 1.5, size=k),
        duration=float(centers[-1] + 0.25),
    )


def _vary(g: Gesture, rng: np.random.Generator, variation: float) -> Gesture:
    """Stroke-level variation between two genuine repetitions by the same person."""
    k = len(g.centers)
    centers = np.sort(g.centers + rng.normal(scale=0.03 * variation, size=k))
    return Gesture(
        centers=centers,
        widths=g.widths * (1 + rng.normal(scale=0.08 * variation, size=k)).clip(0.6, 1.4),
        amps=g.amps * (1 + rng.normal(scale=0.12 * variation, size=k)).clip(0.5, 1.5),
        axes=_normalize(g.axes + rng.normal(scale=0.08 * variation, size=(k, 3))),
        lever=g.lever,
        acc_axes=g.acc_axes,
        acc_amps=g.acc_amps * (1 + rng.normal(scale=0.12 * variation, size=k)),
        duration=g.duration,
    )


def _normalize(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def record(
    g: Gesture,
    rng: np.random.Generator,
    variation: float = 1.0,
    rate_hz: float = 60.0,
    lead_in: tuple[float, float] = (0.6, 0.9),
) -> list[list[float]]:
    """Render one repetition of `g` as phone rows. `variation` scales natural variability."""
    g = _vary(g, rng, variation)
    lead = rng.uniform(*lead_in)
    tail = rng.uniform(0.3, 0.5)
    speed = 1.0 + rng.uniform(-0.12, 0.12) * variation
    total = lead + g.duration * speed + tail

    period = 1000.0 / rate_hz
    steps = period + rng.normal(scale=0.35, size=int(total * rate_hz * 1.2) + 10)
    t_ms = np.cumsum(np.clip(steps, period * 0.5, None))
    t_ms = t_ms[t_ms < total * 1000.0]
    t = t_ms / 1000.0

    # Smooth, monotone time warp inside the gesture (about +-10 percent locally).
    phase = rng.uniform(0, 2 * np.pi)
    wobble = 0.08 * variation * g.duration
    u = np.clip((t - lead) / speed, -1.0, g.duration + 1.0)
    warped = u + wobble * np.sin(2 * np.pi * u / max(g.duration, 1e-3) + phase) * np.exp(
        -0.5 * ((u - g.duration / 2) / g.duration) ** 2
    )

    R = _rotation(rng, 10.0 * variation)
    amp = 1.0 + rng.uniform(-0.1, 0.1, size=3) * variation
    gyro = (g.gyro(warped) @ R.T) * amp
    acc = (g.accel(warped) @ R.T) * amp

    gyro += rng.normal(scale=0.015, size=gyro.shape) + rng.normal(scale=0.004, size=3)
    acc += rng.normal(scale=0.04, size=acc.shape) + rng.normal(scale=0.01, size=3)

    gyro_dps = np.rad2deg(gyro)
    rows = np.column_stack([t_ms - t_ms[0], acc, gyro_dps])
    return np.round(rows, 4).tolist()


def genuine(persona: str, n: int, salt: str = "", **kw) -> list[list[list[float]]]:
    g = base_gesture(persona)
    rng = np.random.default_rng(_seed("genuine", persona, salt))
    return [record(g, rng, **kw) for _ in range(n)]


def imitation(persona: str, imitator: str, n: int, salt: str = "") -> list[list[list[float]]]:
    """An imitator's attempts: one learned copy of the gesture, repeated with their own noise."""
    g = base_gesture(persona)
    rng = np.random.default_rng(_seed("imitation", persona, imitator, salt))
    copy = imitate(g, rng)
    return [record(copy, rng) for _ in range(n)]


def impostor(persona: str, n: int, salt: str = "") -> list[list[list[float]]]:
    return genuine(f"other::{persona}", n, salt=salt)


def lazy_shake(seed: str = "shake") -> list[list[float]]:
    """A trivial back-and-forth shake, which enrollment should reject as too simple."""
    rng = np.random.default_rng(_seed("shake", seed))
    g = Gesture(
        centers=np.array([0.8]),
        widths=np.array([0.35]),
        amps=np.array([1.2]),
        axes=np.array([[0.0, 0.0, 1.0]]),
        lever=np.zeros((3, 3)),
        acc_axes=np.array([[1.0, 0.0, 0.0]]),
        acc_amps=np.array([0.3]),
        duration=1.6,
    )
    return record(g, rng)
