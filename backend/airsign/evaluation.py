"""Evaluation of the verifier on labeled recordings (technical report, section 16).

Input is a list of recordings, each with a participant, a kind and raw rows:
  enroll      enrollment repetitions, used to build each participant's template
  genuine     the participant's own later attempts
  imitation   attempts by `participant` to copy `target`'s signature after watching
Casual impostors are every other participant's genuine attempts against a template.
Replays resubmit the participant's own genuine attempts after they were accepted.

Rates come with Wilson 95% intervals, since five people give small counts.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np

from .signal import enroll, pipeline, replay, synth, verify
from .signal.fingerprint import fingerprint
from .signal.siamese import SiameseModel


@dataclass
class Rec:
    id: str | int
    participant: str
    kind: str  # enroll, genuine, imitation
    samples: list
    target: str | None = None
    platform: str | None = None
    device_label: str | None = None
    session: str | None = None
    posture: str | None = None
    accel_includes_gravity: bool = False
    nominal_hz: float | None = None


@dataclass
class Scored:
    participant: str  # template owner
    by: str  # who produced the attempt
    kind: str  # genuine, imitation, impostor, replay
    z: float  # inf when the recording was unusable
    replay_flagged: bool
    sensor_flags: list[str] = field(default_factory=list)
    platform: str | None = None
    # Raw distances and the template's own statistics, so calibration can
    # recompute z for other floors and methods without running DTW again.
    raw: dict | None = None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def _process(r: Rec):
    try:
        return pipeline.preprocess(r.samples, r.accel_includes_gravity)
    except pipeline.RecordingError:
        return None


def score_all(recs: list[Rec], model: SiameseModel | None = None) -> tuple[list[Scored], dict]:
    by_part: dict[str, list[Rec]] = defaultdict(list)
    for r in recs:
        by_part[r.participant].append(r)

    processed = {id(r): _process(r) for r in recs}
    templates: dict[str, enroll.Template] = {}
    notes: dict[str, str] = {}
    for p, rs in by_part.items():
        reps = [processed[id(r)] for r in rs if r.kind == "enroll" and processed[id(r)] is not None]
        try:
            templates[p], _ = enroll.build_template(reps, model)
        except enroll.EnrollmentError as e:
            notes[p] = e.message

    out: list[Scored] = []
    for owner, t in templates.items():
        accepted_fps = []
        for r in recs:
            if r.kind == "enroll":
                continue
            if r.kind == "genuine" and r.participant == owner:
                kind = "genuine"
            elif r.kind == "imitation" and r.target == owner:
                kind = "imitation"
            elif r.kind == "genuine" and r.participant != owner:
                kind = "impostor"
            else:
                continue
            c = processed[id(r)]
            if c is None:
                out.append(Scored(owner, r.participant, kind, math.inf, False, [], r.platform))
                continue
            s = verify.score(t, c, model)
            flags = replay.replay_flags(t, c, s.min_dtw, accepted_fps if kind == "genuine" else [])
            sensor = replay.sensor_flags(c, r.nominal_hz)
            raw = {"s_dtw": s.s_dtw, "mu_dtw": t.mu_dtw, "sd_dtw": t.sd_dtw, "s_nn": s.s_nn, "mu_nn": t.mu_nn, "sd_nn": t.sd_nn}
            out.append(Scored(owner, r.participant, kind, s.z, bool(flags), sensor, r.platform, raw))
            if kind == "genuine":
                if s.z <= verify.tier_k(1) and not flags:
                    accepted_fps.append(fingerprint(c.X_fixed))
                    # The same recording submitted again is a replay.
                    rf = replay.replay_flags(t, c, s.min_dtw, accepted_fps)
                    out.append(Scored(owner, r.participant, "replay", s.z, bool(rf), sensor, r.platform))
    return out, notes


def eer(gen: list[float], imp: list[float]) -> dict | None:
    gen_a, imp_a = np.array(gen), np.array(imp)
    if not len(gen_a) or not len(imp_a):
        return None
    finite = np.concatenate([gen_a[np.isfinite(gen_a)], imp_a[np.isfinite(imp_a)]])
    if not len(finite):
        return None
    best = None
    for th in np.unique(np.concatenate([finite, [finite.min() - 1, finite.max() + 1]])):
        frr = float(np.mean(gen_a > th))
        far = float(np.mean(imp_a <= th))
        gap = abs(frr - far)
        if best is None or gap < best[0]:
            best = (gap, th, frr, far)
    _, th, frr, far = best
    return {"eer": round((frr + far) / 2, 4), "threshold_z": round(float(th), 3), "frr": round(frr, 4), "far": round(far, 4)}


def roc(gen: list[float], imp: list[float], points: int = 41) -> list[dict]:
    """FRR and FAR across thresholds, for charts."""
    gen_a, imp_a = np.array(gen), np.array(imp)
    out = []
    for th in np.linspace(-2, 8, points):
        out.append({"z": round(float(th), 2), "frr": float(np.mean(gen_a > th)) if len(gen_a) else None, "far": float(np.mean(imp_a <= th)) if len(imp_a) else None})
    return out


def summarize(scored: list[Scored], notes: dict | None = None) -> dict:
    groups: dict[str, list[Scored]] = defaultdict(list)
    for s in scored:
        groups[s.kind].append(s)
    gen = [s.z for s in groups["genuine"]]
    imi = [s.z for s in groups["imitation"]]
    imp = [s.z for s in groups["impostor"]]

    tiers = {}
    for tier, k in ((1, verify.tier_k(1)), (2, verify.tier_k(2))):
        tiers[f"tier{tier}"] = {
            "k": k,
            "genuine_accepted": rate(sum(z <= k for z in gen), len(gen)),
            "imitation_accepted": rate(sum(z <= k for z in imi), len(imi)),
            "impostor_accepted": rate(sum(z <= k for z in imp), len(imp)),
        }

    rep = groups["replay"]
    sensor_fp = [s for s in groups["genuine"] if len(s.sensor_flags) >= 2]
    by_platform: dict[str, dict] = defaultdict(lambda: {"n": 0, "flagged": 0, "flags": defaultdict(int)})
    for s in groups["genuine"]:
        d = by_platform[s.platform or "unknown"]
        d["n"] += 1
        d["flagged"] += len(s.sensor_flags) >= 2
        for f in s.sensor_flags:
            d["flags"][f] += 1

    per_participant = {}
    for p in sorted({s.participant for s in scored}):
        g = [s.z for s in groups["genuine"] if s.participant == p]
        i = [s.z for s in groups["imitation"] if s.participant == p]
        per_participant[p] = {
            "genuine_accepted_tier1": rate(sum(z <= verify.tier_k(1) for z in g), len(g)),
            "imitation_accepted_tier1": rate(sum(z <= verify.tier_k(1) for z in i), len(i)),
        }

    return {
        "counts": {k: len(v) for k, v in groups.items()},
        "tiers": tiers,
        "eer_imitation": eer(gen, imi),
        "eer_impostor": eer(gen, imp),
        "replay_detected": rate(sum(s.replay_flagged for s in rep), len(rep)),
        "genuine_flagged_as_replay": rate(sum(s.replay_flagged for s in groups["genuine"]), len(gen)),
        "genuine_flagged_by_sensor_check": rate(len(sensor_fp), len(gen)),
        "sensor_flags_by_platform": {k: {**v, "flags": dict(v["flags"])} for k, v in by_platform.items()},
        "per_participant": per_participant,
        "roc_imitation": roc(gen, imi),
        "notes": notes or {},
    }


# Datasets ------------------------------------------------------------------------------


def load_export(path: Path) -> list[Rec]:
    """A JSON export from the recording lab."""
    data = json.loads(Path(path).read_text())
    return [
        Rec(
            id=r["id"],
            participant=r["participant"],
            kind=r["kind"],
            samples=r["samples"],
            target=r.get("target"),
            platform=r.get("platform"),
            device_label=r.get("device_label"),
            session=r.get("session"),
            posture=r.get("posture"),
            accel_includes_gravity=r.get("accel_includes_gravity", False),
            nominal_hz=r.get("nominal_hz"),
        )
        for r in data["recordings"]
    ]


def synthetic_dataset(n: int) -> list[Rec]:
    """The report's protocol on synthetic people: 7 enrollment takes, 15 genuine
    attempts, and 10 observed imitations of the next person."""
    people = [f"S{i + 1}" for i in range(n)]
    recs: list[Rec] = []
    for i, p in enumerate(people):
        for k, rows in enumerate(synth.genuine(p, 7, salt="enroll")):
            recs.append(Rec(f"{p}-e{k}", p, "enroll", rows, platform="synthetic"))
        for k, rows in enumerate(synth.genuine(p, 15, salt="later")):
            recs.append(Rec(f"{p}-g{k}", p, "genuine", rows, platform="synthetic", nominal_hz=60))
        target = people[(i + 1) % n]
        for k, rows in enumerate(synth.imitation(target, p, 10)):
            recs.append(Rec(f"{p}-i{k}", p, "imitation", rows, target=target, platform="synthetic", nominal_hz=60))
    return recs
