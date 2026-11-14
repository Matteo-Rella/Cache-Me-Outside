"""Tests for api.py with TestClient in fake mode and a temporary database."""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi.testclient import TestClient

import api
from rewind import config, lectures

FOUND_Q = "coefficient matrix of the system"
MISSING_Q = "Chocolate cake recipe ingredients?"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REWIND_FAKE", "1")
    app = api.create_app(db_path=tmp_path / "api.db")
    assert app.state.mode == "fake"
    return TestClient(app)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _wrong_index(snippet_id, qi=0):
    answer = lectures.get_snippet(snippet_id)["questions"][qi]["answer"]
    return (answer + 1) % 4, answer


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True, "mode": "fake"}


def test_mode_auto_fake_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("REWIND_FAKE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api.create_app(db_path=tmp_path / "x.db").state.mode == "fake"


def test_mode_real_without_key_once_llm_available_exists(tmp_path, monkeypatch, capsys):
    from rewind import llm

    monkeypatch.delenv("REWIND_FAKE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "available", lambda: False, raising=False)
    app = api.create_app(db_path=tmp_path / "x.db")
    assert app.state.mode == "real"
    assert "threshold-only" in capsys.readouterr().out


def test_youtube_id():
    assert api.youtube_id("https://www.youtube.com/watch?v=ZK3O402wf1c") == "ZK3O402wf1c"
    assert api.youtube_id("https://youtu.be/ZK3O402wf1c?t=30") == "ZK3O402wf1c"
    assert api.youtube_id("https://www.youtube.com/embed/ZK3O402wf1c") == "ZK3O402wf1c"
    assert api.youtube_id("media/demo01/video.mp4") is None
    assert api.youtube_id("https://evil.example/watch?v=ZK3O402wf1c") is None


def test_lectures(client):
    data = client.get("/api/lectures").json()
    demo = next(l for l in data if l["lecture_id"] == "demo01")
    assert demo["video_type"] == "youtube"
    assert demo["youtube_id"] == "ZK3O402wf1c"
    assert demo["snippet_count"] == 3
    assert set(demo) == {"lecture_id", "title", "course", "lecture_number", "video_url",
                         "video_type", "youtube_id", "snippet_count"}


def test_examples(client):
    ex = client.get("/api/examples").json()
    assert len(ex) == 3
    concepts = {s["concept"] for s in lectures.all_snippets()}
    assert all(any(c in q for c in concepts) for q in ex)


def test_ask_found(client):
    r = client.post("/api/ask", json={"student_id": "s1", "question": FOUND_Q}).json()
    assert r["status"] == "found"
    assert r["homework"] is False
    assert r["snippet"]["id"] == "demo01-002"
    assert set(r["snippet"]) == {"id", "lecture_id", "concept", "start", "end", "summary", "questions"}
    assert set(r["snippet"]["questions"][0]) == {"q", "options"}
    assert r["lecture"]["lecture_id"] == "demo01"
    for alt in r["alternatives"]:
        assert set(alt) == {"snippet", "lecture"}


def test_ask_not_covered_flags_gap(client):
    r = client.post("/api/ask", json={"student_id": "s1", "question": MISSING_Q}).json()
    assert r == {"status": "not_covered", "homework": False, "snippet": None, "lecture": None, "alternatives": []}
    gaps = client.get("/api/lecturer/gaps").json()
    assert [g["question"] for g in gaps] == [MISSING_Q]


def test_ask_homework_and_validation(client):
    r = client.post("/api/ask", json={"student_id": "s1", "question": "Solve the coefficient matrix system"}).json()
    assert r["homework"] is True
    assert client.post("/api/ask", json={"student_id": "s1", "question": "   "}).status_code == 400
    assert client.post("/api/ask", json={"question": "x"}).status_code == 422


def test_answer_and_rewatch(client):
    wrong, right = _wrong_index("demo01-001")
    r = client.post("/api/answer", json={"student_id": "s1", "snippet_id": "demo01-001",
                                         "question_index": 0, "chosen": wrong}).json()
    assert r["correct"] is False and r["correct_index"] == right and r["explanation"]

    rewatch = client.get("/api/students/s1/rewatch").json()
    assert [x["snippet"]["id"] for x in rewatch] == ["demo01-001"]
    assert rewatch[0]["lecture"]["lecture_id"] == "demo01"

    r = client.post("/api/answer", json={"student_id": "s1", "snippet_id": "demo01-001",
                                         "question_index": 0, "chosen": right}).json()
    assert r["correct"] is True
    assert client.get("/api/students/s1/rewatch").json() == []


def test_answer_errors(client):
    body = {"student_id": "s1", "snippet_id": "demo01-999", "question_index": 0, "chosen": 0}
    assert client.post("/api/answer", json=body).status_code == 404
    body["snippet_id"] = "demo01-001"
    body["question_index"] = 9
    assert client.post("/api/answer", json=body).status_code == 400
    body["question_index"], body["chosen"] = 0, 7
    assert client.post("/api/answer", json=body).status_code == 400


def test_stats_seed_and_gaps(client):
    empty = client.get("/api/lecturer/stats").json()
    assert empty["totals"] == {"asks": 0, "found_pct": 0.0, "not_covered_pct": 0.0, "avg_fail_rate": 0.0}

    assert client.post("/api/lecturer/seed").json() == {"ok": True}
    data = client.get("/api/lecturer/stats").json()
    t = data["totals"]
    assert t["asks"] > 50 and 0 < t["not_covered_pct"] < 20
    assert t["found_pct"] + t["not_covered_pct"] == pytest.approx(100)
    assert 0 < t["avg_fail_rate"] < 1
    row = next(s for s in data["snippets"] if s["snippet_id"] == "demo01-002")
    assert set(row) == {"snippet_id", "concept", "lecture_id", "lecture_title", "start", "end",
                        "times_asked", "attempts", "fail_rate"}
    assert row["lecture_title"].startswith("Lec 1") and row["end"] > row["start"]

    gaps = client.get("/api/lecturer/gaps").json()
    assert len(gaps) == 5
    assert set(gaps[0]) == {"gap_id", "question", "count", "last_asked"}
    assert gaps[0]["count"] == 2
    assert client.post(f"/api/lecturer/gaps/{gaps[0]['gap_id']}/resolve").json() == {"ok": True}
    assert len(client.get("/api/lecturer/gaps").json()) == 4
    assert client.post("/api/lecturer/gaps/99999/resolve").status_code == 404


def test_graph(tmp_path, monkeypatch):
    lec_dir = tmp_path / "lectures"
    shutil.copytree(config.LECTURES_DIR / "demo01", lec_dir / "demo01")
    monkeypatch.setattr(config, "LECTURES_DIR", lec_dir)
    client = TestClient(api.create_app(fake=True, db_path=tmp_path / "g.db"))
    client.post("/api/lecturer/seed")
    g = client.get("/api/graph").json()
    types = {}
    for n in g["nodes"]:
        types.setdefault(n["type"], []).append(n)
    assert len(types["course"]) == len(types["lecture"]) == 1
    assert len(types["snippet"]) == len(types["concept"]) == 3
    hard = next(n for n in types["snippet"] if n["snippet_id"] == "demo01-002")
    assert hard["times_asked"] > 0 and hard["fail_rate"] > 0.4
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    assert len(g["edges"]) == 1 + 3 + 3


def test_graph_shares_concepts_across_lectures(tmp_path, monkeypatch):
    lec_dir = tmp_path / "lectures"
    shutil.copytree(config.LECTURES_DIR / "demo01", lec_dir / "demo01")
    shutil.copytree(config.LECTURES_DIR / "demo01", lec_dir / "demo02")
    snips = json.loads((lec_dir / "demo02" / "snippets.json").read_text(encoding="utf-8"))
    for s in snips:
        s["id"] = s["id"].replace("demo01", "demo02")
        s["lecture_id"] = "demo02"
    snips[0]["concept"] = snips[0]["concept"].upper()
    (lec_dir / "demo02" / "snippets.json").write_text(json.dumps(snips), encoding="utf-8")
    meta = json.loads((lec_dir / "demo02" / "meta.json").read_text(encoding="utf-8"))
    meta["lecture_id"] = "demo02"
    (lec_dir / "demo02" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", lec_dir)

    c = TestClient(api.create_app(fake=True, db_path=tmp_path / "g.db"))
    g = c.get("/api/graph").json()
    kinds = [n["type"] for n in g["nodes"]]
    assert kinds.count("lecture") == 2 and kinds.count("snippet") == 6 and kinds.count("concept") == 3


def test_media_range_and_traversal(tmp_path, monkeypatch):
    lec_dir = tmp_path / "lectures"
    (lec_dir / "vid01").mkdir(parents=True)
    (lec_dir / "vid01" / "video.mp4").write_bytes(bytes(range(256)) * 4)
    (lec_dir / "secret.mp4").write_bytes(b"nope")
    monkeypatch.setattr(config, "LECTURES_DIR", lec_dir)
    c = TestClient(api.create_app(fake=True, db_path=tmp_path / "m.db"))

    full = c.get("/media/vid01/video.mp4")
    assert full.status_code == 200 and len(full.content) == 1024

    part = c.get("/media/vid01/video.mp4", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == bytes(range(10, 20))
    assert part.headers["content-range"] == "bytes 10-19/1024"

    for bad in ("/media/vid01/..%2Fsecret.mp4", "/media/..%2F/secret.mp4", "/media/vid01/missing.mp4",
                "/media/vid01/..", "/media/demo01/meta.json"):
        assert c.get(bad).status_code == 404, bad


def test_no_answer_leaks_before_answer_endpoint(client):
    client.post("/api/lecturer/seed")
    wrong, _ = _wrong_index("demo01-001")
    client.post("/api/answer", json={"student_id": "s1", "snippet_id": "demo01-001",
                                     "question_index": 0, "chosen": wrong})
    responses = [
        client.get("/api/lectures"),
        client.get("/api/examples"),
        client.post("/api/ask", json={"student_id": "s1", "question": FOUND_Q}),
        client.post("/api/ask", json={"student_id": "s1", "question": "three pictures of a linear system"}),
        client.post("/api/ask", json={"student_id": "s1", "question": MISSING_Q}),
        client.get("/api/students/s1/rewatch"),
        client.get("/api/lecturer/stats"),
        client.get("/api/lecturer/gaps"),
        client.get("/api/graph"),
    ]
    full_texts = [s["text"] for s in lectures.all_snippets()]
    for r in responses:
        assert r.status_code == 200, r.url
        keys = set(_keys(r.json()))
        assert not keys & {"answer", "explanation", "text", "correct_index"}, (r.url, keys)
        assert not any(t in r.text for t in full_texts), r.url
