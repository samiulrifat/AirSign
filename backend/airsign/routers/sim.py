"""Simulation mode: labeled synthetic gestures for testing without a phone.

Each user gets a stable synthetic signature (the persona is their user id).
Simulated recordings go through exactly the same pipeline, verifier and replay
checks as real ones. Nothing is bypassed; the payload is only labeled.
"""

from __future__ import annotations

import secrets
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import customer
from ..config import get_settings
from ..signal import synth

router = APIRouter(prefix="/api/sim")

LABELS = {
    "genuine": "Simulated: you, signing normally",
    "imitation": "Simulated: someone who watched you sign and copies it",
    "impostor": "Simulated: a different person's gesture",
    "injection": "Simulated: generated sensor data, too clean to be real",
    "shake": "Simulated: a trivial shake (too simple to enroll)",
}


@router.get("/gesture")
def gesture(
    kind: Literal["genuine", "imitation", "impostor", "injection", "shake"] = "genuine",
    enroll_index: int | None = Query(default=None, ge=0, le=9),
    session=Depends(customer),
):
    if not get_settings().allow_simulation:
        raise HTTPException(403, {"code": "simulation_disabled", "message": "Simulation is turned off on this server."})
    user, _ = session
    persona = user.id
    salt = secrets.token_hex(4)
    if kind == "genuine":
        if enroll_index is not None:
            rows = synth.genuine(persona, enroll_index + 1, salt="enroll")[enroll_index]
        else:
            rows = synth.genuine(persona, 1, salt=salt)[0]
    elif kind == "imitation":
        rows = synth.imitation(persona, f"observer-{salt}", 1, salt=salt)[0]
    elif kind == "impostor":
        rows = synth.impostor(f"{persona}-{salt}", 1)[0]
    elif kind == "shake":
        rows = synth.lazy_shake(salt)
    else:
        rows = _injection(persona)
    return {
        "kind": kind,
        "label": LABELS[kind],
        "simulated": True,
        "device": {"platform": "simulated", "nominal_hz": 60, "accel_includes_gravity": False},
        "samples": rows,
    }


def _injection(persona: str) -> list[list[float]]:
    """The user's own gesture shape, generated mathematically: no noise, perfect timing."""
    g = synth.base_gesture(persona)
    n = int((g.duration + 1.4) * 60)
    t_ms = np.arange(n) * (1000 / 60)
    t = t_ms / 1000 - 0.7
    acc = g.accel(t)
    gyro = np.rad2deg(g.gyro(t))
    return np.round(np.column_stack([t_ms, acc, gyro]), 4).tolist()
