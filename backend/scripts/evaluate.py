"""Evaluate the verifier offline.

    python scripts/evaluate.py airsign-lab.json      # a lab export
    python scripts/evaluate.py --synthetic 5         # the report's protocol on synthetic people

Prints a markdown summary and writes the full result as JSON next to the input
(or to evaluation-synthetic.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airsign import evaluation  # noqa: E402
from airsign.signal.siamese import get_model  # noqa: E402


def pct(r: dict) -> str:
    if not r["n"]:
        return "n/a"
    lo, hi = r["ci95"]
    return f"{100 * r['rate']:.1f}% ({r['k']}/{r['n']}, 95% CI {100 * lo:.0f}–{100 * hi:.0f}%)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export", nargs="?", type=Path)
    ap.add_argument("--synthetic", type=int, metavar="N")
    args = ap.parse_args()
    if args.synthetic:
        recs, out = evaluation.synthetic_dataset(args.synthetic), Path("evaluation-synthetic.json")
    elif args.export:
        recs, out = evaluation.load_export(args.export), args.export.with_suffix(".results.json")
    else:
        ap.error("give a lab export or --synthetic N")

    scored, notes = evaluation.score_all(recs, get_model())
    s = evaluation.summarize(scored, notes)
    out.write_text(json.dumps(s, indent=2))

    print(f"# AirSign evaluation ({'synthetic' if args.synthetic else args.export.name})\n")
    print("Attempts:", ", ".join(f"{k} {v}" for k, v in s["counts"].items()), "\n")
    print("| | Tier 1 (k=2.0) | Tier 2 (k=1.0) |\n|---|---|---|")
    for key, label in (("genuine_accepted", "Genuine accepted"), ("imitation_accepted", "Observed imitation accepted"), ("impostor_accepted", "Other person accepted")):
        print(f"| {label} | {pct(s['tiers']['tier1'][key])} | {pct(s['tiers']['tier2'][key])} |")
    for key, label in (("eer_imitation", "EER vs imitation"), ("eer_impostor", "EER vs other people")):
        e = s[key]
        print(f"\n{label}: " + (f"{100 * e['eer']:.1f}% at z = {e['threshold_z']}" if e else "n/a"))
    print(f"\nReplay detected: {pct(s['replay_detected'])}")
    print(f"Genuine wrongly flagged as replay: {pct(s['genuine_flagged_as_replay'])}")
    print(f"Genuine flagged by the sensor check: {pct(s['genuine_flagged_by_sensor_check'])}")
    for p, note in s["notes"].items():
        print(f"\nNote: {p} could not enroll: {note}")
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    main()
