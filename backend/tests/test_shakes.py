"""A small shake before or after signing must not become part of the signature."""

import numpy as np

from airsign.signal import enroll, pipeline, replay, synth, verify

RATE = 60.0
RECORDING = synth.genuine("shake-user", 1, salt="later")[0]


def quiet(seconds, rng):
    n = int(seconds * RATE)
    return rng.normal(scale=0.04, size=(n, 3)), rng.normal(scale=0.9, size=(n, 3))


def shake(seconds, rng, dps=25.0, acc=0.5, hz=7.0):
    n = int(seconds * RATE)
    wave = np.sin(2 * np.pi * hz * np.arange(n) / RATE) * np.hanning(n)
    axis = np.array([0.6, 0.64, 0.48])
    a, g = quiet(seconds, rng)
    return a + np.outer(wave * acc, axis), g + np.outer(wave * dps, axis)


def recording(*parts):
    acc = np.vstack([p[0] for p in parts])
    gyro = np.vstack([p[1] for p in parts])
    rng = np.random.default_rng(len(acc))
    t = np.cumsum(1000.0 / RATE + rng.normal(scale=0.35, size=len(acc)))
    return np.column_stack([t - t[0], acc, gyro]).tolist()


def signature():
    a = np.array(RECORDING)
    return a[:, 1:4], a[:, 4:7]


def test_shakes_at_either_end_leave_the_same_signature():
    rng = np.random.default_rng(1)
    clean = pipeline.preprocess(RECORDING)
    after = pipeline.preprocess(recording(signature(), quiet(0.25, rng), shake(0.5, rng), quiet(0.25, rng)))
    before = pipeline.preprocess(recording(quiet(0.7, rng), shake(0.5, rng), quiet(0.25, rng), signature()))
    both = pipeline.preprocess(recording(quiet(0.7, rng), shake(0.4, rng), quiet(0.25, rng), signature(), quiet(0.25, rng), shake(0.4, rng), quiet(0.25, rng)))
    for p in (after, before, both):
        assert abs(p.duration_s - clean.duration_s) < 0.1


def test_a_short_sharp_jolt_at_the_end_is_dropped():
    # Too strong for the first test, but short, cut off by a pause and clearly weaker than the signature.
    rng = np.random.default_rng(2)
    clean = pipeline.preprocess(RECORDING)
    jolted = pipeline.preprocess(recording(signature(), quiet(0.3, rng), shake(0.2, rng, dps=190.0, acc=2.0), quiet(0.3, rng)))
    assert abs(jolted.duration_s - clean.duration_s) < 0.1


def test_shaken_signatures_still_match_and_pass_the_sensor_checks():
    rng = np.random.default_rng(3)
    reps = [pipeline.preprocess(r) for r in synth.genuine("shake-user", 7, salt="enroll")]
    template, _ = enroll.build_template(reps)
    clean = verify.score(template, pipeline.preprocess(RECORDING))
    shaken = pipeline.preprocess(recording(quiet(0.7, rng), shake(0.5, rng), quiet(0.25, rng), signature(), quiet(0.25, rng), shake(0.5, rng), quiet(0.25, rng)))
    assert abs(verify.score(template, shaken).z - clean.z) < 0.3
    assert replay.sensor_flags(shaken, None) == []


def test_a_pause_inside_the_signature_is_kept():
    rng = np.random.default_rng(4)
    a = np.array(RECORDING)
    plain = pipeline.preprocess(RECORDING)
    # In the middle of a stroke at each point, so the pause really splits the motion.
    for fraction in (0.6, 0.7):
        cut = int(len(a) * fraction)
        first, second = (a[:cut, 1:4], a[:cut, 4:7]), (a[cut:, 1:4], a[cut:, 4:7])
        paused = pipeline.preprocess(recording(first, quiet(0.4, rng), second))
        assert paused.duration_s > plain.duration_s + 0.25, fraction


def test_a_strong_short_last_stroke_is_kept():
    # Like the dot on an "i": short and after a pause, but as firm as the rest of the signature.
    rng = np.random.default_rng(5)
    clean = pipeline.preprocess(RECORDING)
    dotted = pipeline.preprocess(recording(signature(), quiet(0.3, rng), shake(0.25, rng, dps=240.0, acc=3.0), quiet(0.3, rng)))
    assert dotted.duration_s > clean.duration_s + 0.2


def test_shaking_as_the_whole_signature_is_still_rejected():
    p = pipeline.preprocess(synth.lazy_shake())
    try:
        enroll.check_sample(p)
    except enroll.EnrollmentError:
        return
    raise AssertionError("a lazy shake must not enroll")
