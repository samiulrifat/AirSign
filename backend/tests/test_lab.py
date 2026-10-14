"""The recording lab: access, protocol recording, diagnostics, summary, export and withdrawal."""

from airsign.signal import synth

from .conftest import new_client


def lab():
    c = new_client()
    assert c.post("/api/lab/login", json={"code": "wrong"}).status_code == 401
    assert c.post("/api/lab/login", json={"code": "test-lab"}).status_code == 200
    return c


def record(c, pid, kind, rows, **extra):
    body = {"participant_id": pid, "kind": kind, "samples": rows, "device": {"platform": "synthetic", "nominal_hz": 60}, **extra}
    r = c.post("/api/lab/recordings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_lab_requires_code():
    c = new_client()
    assert c.get("/api/lab/participants").status_code == 401
    assert c.get("/api/lab/status").json() == {"enabled": True, "authorized": False}


def test_protocol_summary_export_and_withdrawal():
    c = lab()
    a = c.post("/api/lab/participants", json={"alias": "Ann", "consent": True}).json()["id"]
    b = c.post("/api/lab/participants", json={"alias": "Ben", "consent": True}).json()["id"]
    assert c.post("/api/lab/participants", json={"alias": "No", "consent": False}).status_code == 400

    for pid in (a, b):
        for rows in synth.genuine(f"lab-{pid}", 7, salt="enroll"):
            d = record(c, pid, "enroll", rows)["diagnostics"]
            assert d["usable"] and d["enroll_quality"] == "ok" and d["sensor_flags"] == []
            assert 55 < d["rate_hz"] < 65

    g = record(c, a, "genuine", synth.genuine(f"lab-{a}", 1, salt="later")[0], session="A", posture="standing")
    assert "score" in g["diagnostics"]
    i = record(c, b, "imitation", synth.imitation(f"lab-{a}", "ben", 1)[0], target_id=a)
    assert i["diagnostics"].get("score", {}).get("tier1") in (None, "REJECT")
    bad = c.post("/api/lab/recordings", json={"participant_id": b, "kind": "imitation", "samples": [[0, 0, 0, 0, 0, 0, 0]] * 40})
    assert bad.json()["code"] == "bad_target"

    progress = {p["id"]: p for p in c.get("/api/lab/participants").json()}
    assert progress[a]["counts"] == {"enroll": 7, "genuine": 1, "imitation": 0}
    assert progress[b]["imitated"] == {a: 1}

    s = c.get("/api/lab/summary").json()
    assert s["counts"]["genuine"] == 1 and s["counts"]["imitation"] == 1 and s["counts"]["impostor"] == 1

    exp = c.get("/api/lab/export")
    assert exp.headers["content-disposition"].startswith("attachment")
    data = exp.json()
    assert data["format"] == "airsign-lab-v1" and len(data["recordings"]) == 16
    assert "alias" not in str(data["participants"])  # export is pseudonymous

    assert c.delete(f"/api/lab/participants/{b}").status_code == 200
    assert len(c.get("/api/lab/export").json()["recordings"]) == 8
