"""The neural scoring path and calibration loading, without needing PyTorch."""

import json
import math

import numpy as np

from airsign.signal import calibration, enroll, pipeline, synth, verify
from airsign.signal.siamese import SiameseModel

SHAPES = {
    "conv1.weight": (32, 6, 7), "conv1.bias": (32,),
    "conv2.weight": (64, 32, 5), "conv2.bias": (64,),
    "conv3.weight": (128, 64, 3), "conv3.bias": (128,),
    "fc.weight": (128, 128), "fc.bias": (128,),
}


def random_model(version="test-1"):
    rng = np.random.default_rng(0)
    p = {k: rng.normal(scale=0.2, size=s) for k, s in SHAPES.items()}
    for i, c in ((1, 32), (2, 64), (3, 128)):
        p[f"bn{i}.weight"], p[f"bn{i}.bias"] = np.ones(c), np.zeros(c)
        p[f"bn{i}.running_mean"], p[f"bn{i}.running_var"] = np.zeros(c), np.ones(c)
    return SiameseModel(p, version)


def test_embedding_is_unit_length_and_deterministic():
    m = random_model()
    x = np.random.default_rng(1).normal(size=(6, 128))
    e = m.embed(x)
    assert e.shape == (128,) and math.isclose(np.linalg.norm(e), 1.0, rel_tol=1e-9)
    assert np.allclose(e, m.embed(x))


def test_fused_scoring_and_version_fallback():
    m = random_model()
    reps = [pipeline.preprocess(r) for r in synth.genuine("nn-user", 7)]
    t, _ = enroll.build_template(reps, m)
    assert t.proto is not None and t.model_version == "test-1"
    t2 = enroll.Template.from_bytes(t.to_bytes())  # survives storage
    assert t2.model_version == "test-1" and np.allclose(t2.proto, t.proto, atol=1e-6)

    c = pipeline.preprocess(synth.genuine("nn-user", 1, salt="later")[0])
    s = verify.score(t2, c, m)
    assert s.method == "fused" and s.z_nn is not None and math.isfinite(s.z)
    # Templates made with another model version fall back to DTW alone.
    assert verify.score(t2, c, random_model("test-2")).method == "dtw"


def test_calibration_file(tmp_path):
    good = tmp_path / "t.json"
    cal = calibration.Calibration(tier_k={1: 4.0, 2: 2.5, 3: 2.5}, method="dtw", sigma_floor_fraction=0.75)
    good.write_text(cal.to_json())
    loaded = calibration.load(good)
    assert loaded.k(1) == 4.0 and loaded.method == "dtw" and loaded.sigma_floor_fraction == 0.75
    assert verify.decide(3.9, 1, loaded) == "STEP_UP" and verify.decide(3.0, 1, loaded) == "ACCEPT"

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tier_k": {"1": 1}, "method": "magic"}))
    assert calibration.load(bad) == calibration.Calibration()
    assert calibration.load(tmp_path / "missing.json") == calibration.Calibration()
