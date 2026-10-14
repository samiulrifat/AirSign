"""Choose the verifier's thresholds from labeled recordings.

    python scripts/calibrate.py airsign-lab.json            # report only
    python scripts/calibrate.py airsign-lab.json --write    # also write models/thresholds.json
    python scripts/calibrate.py --synthetic 8

What it chooses:
  sigma floor and scoring method (dtw, nn, fused, and): lowest equal error rate
      between genuine attempts and observed imitations
  tier 1 k: accepts 97 percent of genuine attempts, but never lets more than
      --tier1-far of imitations through (default 10 percent, the report's target)
  tier 2 and 3 k: accepts 90 percent of genuine attempts, capped at --tier2-far
      (default 2 percent)

The rates it reports are leave-one-person-out: each person's attempts are judged
with thresholds chosen on everyone else, which is what a new customer gets. In-
sample numbers would flatter the system.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airsign import evaluation  # noqa: E402
from airsign.signal import calibration  # noqa: E402
from airsign.signal.siamese import get_model  # noqa: E402

FLOORS = [0.15, 0.25, 0.35, 0.5, 0.75, 1.0, 1.5]
ABS_FLOORS = [0.0, 0.15, 0.3, 0.45, 0.6]  # DTW distance units
# Share of genuine attempts each tier aims to accept when the attack cap allows it.
GENUINE_TARGET = {1: 0.97, 2: 0.90}
K_MAX = 25.0


def z_of(s: evaluation.Scored, floor: float, method: str, w: float = 0.5, abs_floor: float = 0.0) -> float:
    if s.raw is None:
        return math.inf  # unusable recording: always a rejection
    r = s.raw
    zd = (r["s_dtw"] - r["mu_dtw"]) / max(r["sd_dtw"], floor * r["mu_dtw"], abs_floor, 1e-6)
    if method == "dtw" or r["s_nn"] is None:
        return zd
    zn = (r["s_nn"] - r["mu_nn"]) / max(r["sd_nn"], floor * r["mu_nn"], 1e-6)
    return {"nn": zn, "and": max(zd, zn), "fused": w * zd + (1 - w) * zn}[method]


def k_for_far(attack: np.ndarray, far: float, fallback: float) -> float:
    """Largest k with at most `far` of the attack attempts at or under it."""
    attack = np.sort(attack[np.isfinite(attack)])
    if not len(attack):
        return fallback
    allowed = int(math.floor(far * len(attack)))
    if allowed >= len(attack):
        return K_MAX
    return float(min(K_MAX, np.nextafter(attack[allowed], -np.inf)))


def choose(scored: list[evaluation.Scored], methods: list[str], far1: float, far2: float) -> calibration.Calibration:
    best = None
    defaults = calibration.Calibration()
    for method in methods:
        for floor in FLOORS:
            for absf in ABS_FLOORS:
                gen = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "genuine"]
                imi = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "imitation"]
                imp = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "impostor"]
                e = evaluation.eer(gen, imi or imp)
                if e is None:
                    continue
                e2 = evaluation.eer(gen, imp)
                # On equal error, stay near the defaults.
                near = abs(floor - defaults.sigma_floor_fraction) + abs(absf - defaults.sigma_floor_abs)
                key = (e["eer"], e2["eer"] if e2 else 1.0, near)
                if best is None or key < best[0]:
                    best = (key, method, floor, absf)
    if best is None:
        return calibration.Calibration(source="defaults: not enough data to calibrate")
    _, method, floor, absf = best
    attack_kind = "imitation" if any(x.kind == "imitation" for x in scored) else "impostor"
    attack = np.array([z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == attack_kind])
    gen = np.array([z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "genuine"])
    gen = gen[np.isfinite(gen)]
    # No more lenient than needed: accept the target share of genuine attempts,
    # but never past the attack cap. When the two do not overlap, k lands on the
    # genuine side of the gap instead of at the edge of the attacks.
    k1 = min(k_for_far(attack, far1, 2.0), float(np.quantile(gen, GENUINE_TARGET[1])) if len(gen) else 2.0)
    k2 = min(k1, k_for_far(attack, far2, 1.0), float(np.quantile(gen, GENUINE_TARGET[2])) if len(gen) else 1.0)
    return calibration.Calibration(
        tier_k={1: round(k1, 3), 2: round(k2, 3), 3: round(k2, 3)},
        step_up_margin=round(min(0.5, 0.25 * k1), 3),
        sigma_floor_fraction=floor,
        sigma_floor_abs=absf,
        method=method,
    )


def rates(scored: list[evaluation.Scored], cal: calibration.Calibration) -> dict:
    out = {}
    for tier in (1, 2):
        k = cal.k(tier)
        row = {}
        for kind in ("genuine", "imitation", "impostor"):
            zs = [z_of(s, cal.sigma_floor_fraction, cal.method, cal.fusion_w, cal.sigma_floor_abs) for s in scored if s.kind == kind]
            row[kind] = (sum(z <= k for z in zs), len(zs))
        out[tier] = row
    return out


def held_out(scored: list[evaluation.Scored], methods: list[str], far1: float, far2: float) -> dict:
    """Leave one person out: thresholds from everyone else, judged on that person's templates."""
    people = sorted({s.participant for s in scored})
    totals = {t: {k: [0, 0] for k in ("genuine", "imitation", "impostor")} for t in (1, 2)}
    for p in people:
        train = [s for s in scored if s.participant != p and s.by != p]
        test = [s for s in scored if s.participant == p]
        cal = choose(train, methods, far1, far2)
        for tier, row in rates(test, cal).items():
            for kind, (k, n) in row.items():
                totals[tier][kind][0] += k
                totals[tier][kind][1] += n
    return totals


def fmt(k: int, n: int) -> str:
    if not n:
        return "n/a"
    lo, hi = evaluation.wilson(k, n)
    return f"{100 * k / n:.1f}% ({k}/{n}, CI {100 * lo:.0f}–{100 * hi:.0f}%)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", type=Path)
    ap.add_argument("--synthetic", type=int, metavar="N")
    ap.add_argument("--tier1-far", type=float, default=0.10)
    ap.add_argument("--tier2-far", type=float, default=0.02)
    ap.add_argument("--write", action="store_true", help=f"write {calibration.PATH}")
    args = ap.parse_args()
    if args.synthetic:
        recs, name = evaluation.synthetic_dataset(args.synthetic), f"synthetic ({args.synthetic} people)"
    elif args.export:
        recs, name = evaluation.load_export(args.export), args.export.name
    else:
        ap.error("give a lab export or --synthetic N")

    model = get_model()
    methods = ["dtw"] + (["nn", "fused", "and"] if model else [])
    scored, notes = evaluation.score_all(recs, model)
    people = {s.participant for s in scored}
    cal = choose(scored, methods, args.tier1_far, args.tier2_far)
    cal.source = f"scripts/calibrate.py on {name}, {len(people)} people, {datetime.now(timezone.utc):%Y-%m-%d}"

    print(f"# Calibration from {name}\n")
    print("| Method | EER vs imitation | EER vs other people |\n|---|---|---|")
    for method in methods:
        rows = []
        for floor in FLOORS:
            for absf in ABS_FLOORS:
                gen = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "genuine"]
                imi = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "imitation"]
                imp = [z_of(s, floor, method, abs_floor=absf) for s in scored if s.kind == "impostor"]
                e1, e2 = evaluation.eer(gen, imi), evaluation.eer(gen, imp)
                rows.append((e1["eer"] if e1 else math.nan, e2["eer"] if e2 else math.nan))
        b1, b2 = min(rows)
        print(f"| {method} | {100 * b1:.1f}% | {100 * b2:.1f}% |")
    print()
    print(f"Method: {cal.method} · sigma floor {cal.sigma_floor_fraction}×mu, at least {cal.sigma_floor_abs} · tier 1 k = {cal.k(1)} · tier 2/3 k = {cal.k(2)} · step-up margin {cal.step_up_margin}\n")
    if len(people) < 3:
        print("Warning: fewer than 3 people; these thresholds will not generalise.\n")
    ho = held_out(scored, methods, args.tier1_far, args.tier2_far) if len(people) >= 3 else None
    print("| Leave-one-person-out | Tier 1 | Tier 2 |\n|---|---|---|")
    for kind, label in (("genuine", "Genuine accepted"), ("imitation", "Observed imitation accepted"), ("impostor", "Other person accepted")):
        if ho:
            print(f"| {label} | {fmt(*ho[1][kind])} | {fmt(*ho[2][kind])} |")
    for p, note in notes.items():
        print(f"\nNote: {p} could not enroll: {note}")
    if args.write:
        calibration.PATH.parent.mkdir(parents=True, exist_ok=True)
        calibration.PATH.write_text(cal.to_json())
        print(f"\nWrote {calibration.PATH}. Restart the server to use it.")
    else:
        print("\nDry run. Add --write to save these thresholds.")


if __name__ == "__main__":
    main()
