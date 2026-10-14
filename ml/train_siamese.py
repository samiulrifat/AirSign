"""Train the Siamese 1D CNN gesture embedder (technical report, section 8.3).

    python ml/train_siamese.py --synthetic 400                    # pretrain on synthetic people
    python ml/train_siamese.py --lab airsign-lab.json --synthetic 400
    python ml/train_siamese.py ... --install                      # also copy to the server

Data are split by person into train, validation and test people, so the scores
below are for people the model has never seen, like a new customer. Lab
recordings are mixed in by participant. Synthetic people come from the same
generator as simulation mode, so a model trained only on them proves the
pipeline, not real-world accuracy.

The server runs the forward pass in NumPy (backend/airsign/signal/siamese.py);
this script checks both give the same embeddings before exporting.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from airsign import evaluation  # noqa: E402
from airsign.signal import pipeline, synth  # noqa: E402
from airsign.signal.siamese import WEIGHTS_PATH, SiameseModel  # noqa: E402

OUT_DIR = ROOT / "ml" / "out"


class Embedder(nn.Module):
    """Layer names match the NumPy forward pass on the server."""

    def __init__(self, dim: int = 128):
        super().__init__()
        self.conv1, self.bn1 = nn.Conv1d(6, 32, 7, padding=3), nn.BatchNorm1d(32)
        self.conv2, self.bn2 = nn.Conv1d(32, 64, 5, padding=2), nn.BatchNorm1d(64)
        self.conv3, self.bn3 = nn.Conv1d(64, 128, 3, padding=1), nn.BatchNorm1d(128)
        self.fc = nn.Linear(128, dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = F.max_pool1d(F.relu(self.bn1(self.conv1(x))), 2)
        h = F.max_pool1d(F.relu(self.bn2(self.conv2(h))), 2)
        h = F.relu(self.bn3(self.conv3(h)))
        return F.normalize(self.fc(h.mean(dim=2)), dim=1)


# Data -------------------------------------------------------------------------------


def fixed(rows, gravity=False) -> np.ndarray | None:
    try:
        return pipeline.preprocess(rows, gravity).X_fixed.astype(np.float32)
    except pipeline.RecordingError:
        return None


def build_dataset(n_synth: int, lab: list[Path], seed: int) -> dict:
    """Returns {person: {"genuine": [X...], "imitation": [X...]}} with X of shape 6 x 128."""
    people: dict[str, dict[str, list]] = defaultdict(lambda: {"genuine": [], "imitation": []})
    for path in lab:
        for r in evaluation.load_export(path):
            x = fixed(r.samples, r.accel_includes_gravity)
            if x is None:
                continue
            if r.kind in ("enroll", "genuine"):
                people[f"lab:{path.stem}:{r.participant}"]["genuine"].append(x)
            elif r.kind == "imitation" and r.target:
                people[f"lab:{path.stem}:{r.target}"]["imitation"].append(x)
    for i in range(n_synth):
        p = f"syn{seed}-{i}"
        people[p]["genuine"] = [x for x in (fixed(r) for r in synth.genuine(p, 10, salt="train")) if x is not None]
        people[p]["imitation"] = [x for x in (fixed(r) for r in synth.imitation(p, f"obs{i}", 3)) if x is not None]
    return {p: d for p, d in people.items() if len(d["genuine"]) >= 6}


def augment(x: torch.Tensor, rng: torch.Generator) -> torch.Tensor:
    """Time warp about 10 percent, amplitude about 5 percent, noise, small grip rotations."""
    b, c, n = x.shape
    speed = 1 + (torch.rand(b, 1, generator=rng) - 0.5) * 0.2
    grid = (torch.linspace(-1, 1, n).unsqueeze(0) * speed).clamp(-1, 1)
    warp = torch.stack([grid, torch.zeros_like(grid)], dim=-1).unsqueeze(1)  # b,1,n,2
    x = F.grid_sample(x.unsqueeze(2), warp, align_corners=True).squeeze(2)
    x = x * (1 + (torch.rand(b, c, 1, generator=rng) - 0.5) * 0.1)
    angle = (torch.rand(b, generator=rng) - 0.5) * math.radians(16)
    cos, sin = torch.cos(angle), torch.sin(angle)
    rot = torch.zeros(b, 3, 3)
    rot[:, 0, 0], rot[:, 0, 1], rot[:, 1, 0], rot[:, 1, 1], rot[:, 2, 2] = cos, -sin, sin, cos, 1
    x = torch.cat([torch.bmm(rot, x[:, :3]), torch.bmm(rot, x[:, 3:])], dim=1)
    return x + 0.03 * torch.randn(x.shape, generator=rng)


def batches(people: dict, names: list[str], P: int, K: int, steps: int, rng: np.random.Generator):
    for _ in range(steps):
        chosen = rng.choice(names, size=min(P, len(names)), replace=False)
        xs, ys = [], []
        for label, p in enumerate(chosen):
            g = people[p]["genuine"]
            for j in rng.choice(len(g), size=K, replace=len(g) < K):
                xs.append(g[j])
                ys.append(label)
        yield torch.from_numpy(np.stack(xs)), torch.tensor(ys)


def semi_hard_triplet_loss(emb: torch.Tensor, labels: torch.Tensor, margin: float) -> torch.Tensor:
    """For each anchor-positive pair, the hardest negative that is still farther than the positive
    (semi-hard); falls back to the hardest negative when none is."""
    d = torch.cdist(emb, emb).pow(2)
    same = labels[:, None] == labels[None, :]
    eye = torch.eye(len(labels), dtype=torch.bool)
    pos_mask = same & ~eye
    neg_mask = ~same
    losses = []
    for a, p in pos_mask.nonzero():
        dap = d[a, p]
        dn = d[a][neg_mask[a]]
        semi = dn[(dn > dap) & (dn < dap + margin)]
        dan = semi.min() if len(semi) else dn.min()
        losses.append(F.relu(dap - dan + margin))
    return torch.stack(losses).mean() if losses else emb.sum() * 0


# Evaluation on unseen people ------------------------------------------------------------


@torch.no_grad()
def embed_all(model: Embedder, xs: list[np.ndarray]) -> np.ndarray:
    if not xs:
        return np.zeros((0, 128))
    return model(torch.from_numpy(np.stack(xs))).numpy()


@torch.no_grad()
def score(model: Embedder, people: dict, names: list[str], n_enroll: int = 5) -> dict:
    """Prototype from the first takes; genuine = the rest, impostor = other people, imitation if any."""
    model.eval()
    protos, gen, imi = {}, [], []
    embs = {p: embed_all(model, people[p]["genuine"]) for p in names}
    for p in names:
        e = embs[p]
        proto = e[:n_enroll].mean(axis=0)
        protos[p] = proto / np.linalg.norm(proto)
        gen += list(1 - e[n_enroll:] @ protos[p])
        imi += list(1 - embed_all(model, people[p]["imitation"]) @ protos[p])
    imp = [float(1 - embs[q][k] @ protos[p]) for p in names for q in names if q != p for k in range(n_enroll, len(embs[q]))]
    return {
        "eer_impostor": (evaluation.eer(gen, imp) or {}).get("eer"),
        "eer_imitation": (evaluation.eer(gen, imi) or {}).get("eer") if imi else None,
    }


def export(model: Embedder, path: Path) -> str:
    sd = {k: v.detach().cpu().numpy().astype(np.float32) for k, v in model.state_dict().items() if "num_batches" not in k}
    digest = hashlib.sha256(b"".join(v.tobytes() for v in sd.values())).hexdigest()[:12]
    version = f"siamese-{digest}"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, version=np.array(version), **sd)
    return version


@torch.no_grad()
def check_numpy_parity(model: Embedder, path: Path) -> float:
    model.eval()
    with np.load(path) as data:
        params = {k: data[k].astype(np.float64) for k in data.files if k != "version"}
    npm = SiameseModel(params, "check")
    x = np.random.default_rng(0).normal(size=(4, 6, 128)).astype(np.float32)
    ref = model(torch.from_numpy(x)).numpy()
    ours = np.stack([npm.embed(xi.astype(np.float64)) for xi in x])
    return float(np.abs(ref - ours).max())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", type=int, default=0, metavar="N", help="synthetic people to add")
    ap.add_argument("--lab", type=Path, action="append", default=[], help="lab export(s)")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--steps", type=int, default=60, help="batches per epoch")
    ap.add_argument("--margin", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--install", action="store_true", help=f"copy the weights to {WEIGHTS_PATH}")
    args = ap.parse_args()
    if not args.synthetic and not args.lab:
        ap.error("give --synthetic N and/or --lab export.json")

    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    trng = torch.Generator().manual_seed(args.seed)
    t0 = time.time()
    people = build_dataset(args.synthetic, args.lab, args.seed)
    names = sorted(people)
    rng.shuffle(names)
    n = len(names)
    test, val, train = names[: max(2, n // 10)], names[max(2, n // 10) : max(4, n // 5)], names[max(4, n // 5) :]
    print(f"{n} people ({sum(len(d['genuine']) for d in people.values())} genuine takes) in {time.time() - t0:.0f}s; "
          f"train {len(train)}, validation {len(val)}, test {len(test)}")

    model = Embedder()
    print(f"parameters: {sum(p.numel() for p in model.parameters()):,}")
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    best = (math.inf, None)
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for x, y in batches(people, train, P=16, K=4, steps=args.steps, rng=rng):
            loss = semi_hard_triplet_loss(model(augment(x, trng)), y, args.margin)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss.detach())
        sched.step()
        v = score(model, people, val)
        key = (v["eer_imitation"] if v["eer_imitation"] is not None else 0) + v["eer_impostor"]
        if key < best[0]:
            best = (key, {k: t.clone() for k, t in model.state_dict().items()})
        if epoch == 1 or epoch % 5 == 0 or epoch == args.epochs:
            print(f"epoch {epoch:3d}  loss {total / args.steps:.4f}  val EER impostor {v['eer_impostor']:.3f}"
                  + (f"  imitation {v['eer_imitation']:.3f}" if v["eer_imitation"] is not None else ""))

    model.load_state_dict(best[1])
    t = score(model, people, test)
    print(f"\nTest people (never seen): EER vs other people {t['eer_impostor']:.3f}"
          + (f", vs imitations {t['eer_imitation']:.3f}" if t["eer_imitation"] is not None else ""))

    out = OUT_DIR / "siamese.npz"
    version = export(model, out)
    diff = check_numpy_parity(model, out)
    print(f"exported {out} ({version}); max difference PyTorch vs NumPy: {diff:.2e}")
    if diff > 1e-4:
        raise SystemExit("NumPy inference does not match PyTorch; not installing.")
    if args.install:
        WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(out, WEIGHTS_PATH)
        print(f"installed to {WEIGHTS_PATH}. Users must enroll again to get neural templates.")


if __name__ == "__main__":
    main()
