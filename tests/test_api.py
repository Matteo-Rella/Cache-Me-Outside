"""Tests for api.py with TestClient in fake mode and a temporary database."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api
from rewind import config, lectures

DEMO_SRC = config.LECTURES_DIR / "demo01"
FOUND_Q = "coefficient matrix of the system"
MISSING_Q = "Chocolate cake recipe ingredients?"


def _copy_demo(dest, hidden=False, lecture_id="demo01"):
    """Copy the demo01 fixture (hidden in the repo) to dest, visible unless hidden=True."""
    shutil.copytree(DEMO_SRC, dest)
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    meta.pop("hidden", None)
    meta["lecture_id"] = lecture_id
    if hidden:
        meta["hidden"] = True
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def lec_dir(tmp_path, monkeypatch):
    """Lectures dir with only a visible copy of demo01."""
    d = tmp_path / "lectures"
    _copy_demo(d / "demo01")
    monkeypatch.setattr(config, "LECTURES_DIR", d)
    return d


@pytest.fixture
def client(tmp_path, monkeypatch, lec_dir):
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
    assert client.get("/api/health").json() == {"ok": True, "mode": "fake", "status": "ready"}


def test_mode_auto_fake_when_search_cannot_import(tmp_path, monkeypatch):
    import sys

    import rewind

    monkeypatch.delenv("REWIND_FAKE", raising=False)
    monkeypatch.delattr(rewind, "search", raising=False)
    monkeypatch.setitem(sys.modules, "rewind.search", None)
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


def _set_concepts(lec_dir, lecture_id, concepts):
    path = lec_dir / lecture_id / "snippets.json"
    snippets = json.loads(path.read_text(encoding="utf-8"))
    for s, c in zip(snippets, concepts):
        s["concept"] = c
    path.write_text(json.dumps(snippets), encoding="utf-8")


def test_examples_natural_one_per_visible_lecture(client, lec_dir):
    _set_concepts(lec_dir, "demo01", ["What is microeconomics", "Opportunity cost", "Demand curve"])
    _copy_demo(lec_dir / "lecb", lecture_id="lecb")
    _set_concepts(lec_dir, "lecb", ["The Astonishing Hypothesis", "Split brain: crossover", "Neural networks"])
    _copy_demo(lec_dir / "hid01", hidden=True, lecture_id="hid01")
    _set_concepts(lec_dir, "hid01", ["Supply curve", "Supply curve", "Supply curve"])
    _copy_demo(lec_dir / "lecc", lecture_id="lecc")
    _set_concepts(lec_dir, "lecc", ["Kidney auction on eBay", "Positive vs. normative analysis",
                                    "Adam Smith's invisible hand"])
    assert client.get("/api/examples").json() == ["What is opportunity cost?", "What are neural networks?"]


@pytest.mark.parametrize("concept,context,expected", [
    ("Opportunity cost", "", "What is opportunity cost?"),
    ("Demand curve", "", "What is the demand curve?"),
    ("Water-diamond paradox", "", "What is the water-diamond paradox?"),
    ("Neural networks", "", "What are neural networks?"),
    ("Dualism", "", "What is dualism?"),
    ("Behavioral economics", "", "What is behavioral economics?"),
    ("Synaptic transmission", "", "What is synaptic transmission?"),
    ("The cortex", "", "What is the cortex?"),
    ("GDP growth", "", "What is GDP growth?"),
    ("L-DOPA", "", "What is L-DOPA?"),
    ("The Astonishing Hypothesis", "Crick called it The Astonishing Hypothesis in his book.",
     "What is the Astonishing Hypothesis?"),
    ("Broca area", "Damage to the area Broca found. Then Broca showed", "What is the Broca area?"),
    ("Freud", "As Freud said", None),
    ("What is microeconomics", "", None),
    ("Positive vs. normative analysis", "", None),
    ("Agonists and antagonists", "", None),
    ("Kidney auction on eBay", "", None),
    ("Split brain: crossover and the corpus callosum", "", None),
    ("Adam Smith's invisible hand", "", None),
    ("Three levels of understanding a model", "", None),
    ("", "", None),
])
def test_example_question(concept, context, expected):
    assert api.example_question(concept, context) == expected


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
    assert r == {"status": "not_covered", "homework": False, "snippet": None, "lecture": None, "alternatives": [],
                 "slides": []}
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
    assert empty["totals"] == {"asks": 0, "found_pct": 0.0, "not_covered_pct": 0.0, "slide_only_pct": 0.0,
                               "avg_fail_rate": 0.0, "followups": 0}

    assert client.post("/api/lecturer/seed").json() == {"ok": True}
    data = client.get("/api/lecturer/stats").json()
    t = data["totals"]
    assert t["asks"] > 50 and 0 < t["not_covered_pct"] < 20
    assert t["found_pct"] + t["not_covered_pct"] == pytest.approx(100)
    assert 0 < t["avg_fail_rate"] < 1
    row = next(s for s in data["snippets"] if s["snippet_id"] == "demo01-002")
    assert set(row) == {"snippet_id", "concept", "lecture_id", "lecture_title", "start", "end",
                        "times_asked", "attempts", "fail_rate", "followups"}
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
    _copy_demo(lec_dir / "demo01")
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
    _copy_demo(lec_dir / "demo01")
    _copy_demo(lec_dir / "demo02", lecture_id="demo02")
    snips = json.loads((lec_dir / "demo02" / "snippets.json").read_text(encoding="utf-8"))
    for s in snips:
        s["id"] = s["id"].replace("demo01", "demo02")
        s["lecture_id"] = "demo02"
    snips[0]["concept"] = snips[0]["concept"].upper()
    (lec_dir / "demo02" / "snippets.json").write_text(json.dumps(snips), encoding="utf-8")
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


# ------------------------------------------------------------------ round 3 fixes

def test_warmup_reports_warming_until_ready(tmp_path, lec_dir):
    import threading

    gate = threading.Event()
    app = api.create_app(fake=True, db_path=tmp_path / "w.db", warmer=gate.wait)
    with TestClient(app) as c:
        assert c.get("/api/health").json()["status"] == "warming"
        gate.set()
        for _ in range(100):
            if c.get("/api/health").json()["status"] == "ready":
                break
            threading.Event().wait(0.02)
        assert c.get("/api/health").json() == {"ok": True, "mode": "fake", "status": "ready"}


def test_warmup_error_is_reported(tmp_path, lec_dir):
    import threading

    def boom():
        raise RuntimeError("no model")

    with TestClient(api.create_app(fake=True, db_path=tmp_path / "w.db", warmer=boom)) as c:
        for _ in range(100):
            h = c.get("/api/health").json()
            if h["status"] != "warming":
                break
            threading.Event().wait(0.02)
        assert h["status"] == "error" and "no model" in h["error"]


def test_hidden_lectures_are_not_shown_to_students(tmp_path, monkeypatch):
    d = tmp_path / "lectures"
    _copy_demo(d / "demo01", hidden=True)
    monkeypatch.setattr(config, "LECTURES_DIR", d)
    monkeypatch.setenv("REWIND_FAKE", "1")
    c = TestClient(api.create_app(db_path=tmp_path / "h.db"))
    assert c.get("/api/lectures").json() == []
    assert c.get("/api/examples").json() == []
    assert c.get("/api/graph").json() == {"nodes": [], "edges": []}
    r = c.post("/api/ask", json={"student_id": "s1", "question": FOUND_Q}).json()
    assert r["status"] == "not_covered" and r["snippet"] is None
    # Tests and the lecturer's tools can still use the hidden lecture directly.
    assert lectures.get_snippet("demo01-002") is not None
    assert c.post("/api/answer", json={"student_id": "s1", "snippet_id": "demo01-002",
                                       "question_index": 0, "chosen": 0}).status_code == 200


def test_hidden_alternatives_are_dropped(client, monkeypatch, lec_dir):
    _copy_demo(lec_dir / "hid01", hidden=True, lecture_id="hid01")
    best = lectures.get_snippet("demo01-002")
    hidden_alt = dict(lectures.get_snippet("demo01-001"), id="hid01-001", lecture_id="hid01")
    from tests import fakes

    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "found", "best": best, "alternatives": [hidden_alt, lectures.get_snippet("demo01-003")],
        "score": 0.9})
    r = client.post("/api/ask", json={"student_id": "s1", "question": "x"}).json()
    assert r["snippet"]["id"] == "demo01-002"
    assert [a["snippet"]["id"] for a in r["alternatives"]] == ["demo01-003"]


def test_repo_demo01_is_hidden():
    meta = json.loads((DEMO_SRC / "meta.json").read_text(encoding="utf-8"))
    assert meta["hidden"] is True


# ------------------------------------------------------------------ round 4: local-only + AI settings

KEY = "sk-or-v1-0123456789abcdefSECRETa1b2"
LOCAL = ("127.0.0.1", 50000)
REMOTE = ("192.168.1.23", 50000)


@pytest.fixture
def fake_settings(monkeypatch):
    import sys
    import types

    data = {"mode": "api", "provider": "openai_compatible", "model": "m1", "api_key": KEY,
            "base_url": "https://openrouter.ai/api/v1"}
    mod = types.ModuleType("rewind.settings")
    mod.data = data
    mod.load = lambda: dict(data)
    mod.save = lambda d: data.update(d)
    mod.public = lambda: {**data, "preset": "openrouter",
                          "api_key": ("sk-…" + data["api_key"][-4:]) if data.get("api_key") else ""}
    monkeypatch.setitem(sys.modules, "rewind.settings", mod)
    return mod


@pytest.fixture
def fake_package(monkeypatch):
    import sys
    import types

    calls = {}
    mod = types.ModuleType("rewind.context_package")
    mod.build_package = lambda lid: b"PK\x03\x04zip-for-" + lid.encode()
    mod.build_chat_prompt = lambda lid, part=None: [f"part 1 for {lid}", "part 2"]

    def import_answer(lid, raw_text):
        calls["import"] = (lid, raw_text)
        if "good" in raw_text:
            return {"ok": True, "snippets_count": 3, "errors": [], "fix_message": ""}
        return {"ok": False, "snippets_count": 0, "errors": ["snippet 1: answer index 7 is out of range"],
                "fix_message": "Your answer had these problems: ... Send the corrected JSON only."}

    mod.import_answer = import_answer
    mod.calls = calls
    monkeypatch.setitem(sys.modules, "rewind.context_package", mod)
    return mod


LOCAL_ONLY = [
    ("get", "/api/settings", None),
    ("post", "/api/settings", {"mode": "none"}),
    ("post", "/api/settings/test", None),
    ("get", "/api/lectures/demo01/package", None),
    ("get", "/api/lectures/demo01/prompt", None),
    ("post", "/api/lectures/demo01/import", {"raw_text": "good"}),
    ("post", "/api/lectures/demo01/generate", None),
    ("get", "/api/lectures/demo01/generate/status", None),
    ("get", "/api/lectures/demo01/generate/estimate", None),
    ("post", "/api/lectures/demo01/slides", None),
]


def _local_app(tmp_path):
    return api.create_app(fake=True, db_path=tmp_path / "l.db")


def _call(c, method, url, body=None, **kw):
    if body is not None:
        kw["json"] = body
    return getattr(c, method)(url, **kw)


@pytest.mark.parametrize("method,url,body", LOCAL_ONLY)
def test_local_only_guard_blocks_remote_clients(tmp_path, lec_dir, fake_settings, fake_package, method, url, body):
    app = _local_app(tmp_path)
    remote = TestClient(app, client=REMOTE)
    r = _call(remote, method, url, body)
    assert r.status_code == 403
    assert KEY not in r.text
    # X-Forwarded-For can't be used to pretend to be local.
    r = _call(remote, method, url, body, headers={"X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403
    # Public endpoints still work for remote clients (phones).
    assert remote.get("/api/lectures").status_code == 200
    assert remote.get("/api/ai-status").status_code == 200


def test_is_loopback():
    assert api.is_loopback("127.0.0.1") and api.is_loopback("127.5.0.1") and api.is_loopback("::1")
    assert api.is_loopback("::ffff:127.0.0.1")
    for host in ("192.168.1.2", "10.0.0.1", "0.0.0.0", "testclient", "localhost", "", None):
        assert not api.is_loopback(host), host


def test_settings_html_is_local_only(tmp_path, lec_dir, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("student", encoding="utf-8")
    (web / "settings.html").write_text("SETTINGS PAGE", encoding="utf-8")
    monkeypatch.setattr(api, "WEB_DIR", web)
    app = _local_app(tmp_path)
    remote, local = TestClient(app, client=REMOTE), TestClient(app, client=LOCAL)
    assert remote.get("/").status_code == 200
    for path in ("/settings.html", "/SETTINGS.HTML", "/./settings.html", "/js/../settings.html",
                 "/settings.html.", "/Settings.Html"):
        r = remote.get(path)
        assert r.status_code in (403, 404) and "SETTINGS PAGE" not in r.text, path
    assert remote.get("/settings.html").status_code == 403
    r = local.get("/settings.html")
    assert r.status_code == 200 and r.text == "SETTINGS PAGE"


def test_settings_get_and_post_never_return_the_key(tmp_path, lec_dir, fake_settings):
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.get("/api/settings")
    assert r.status_code == 200 and KEY not in r.text
    assert r.json()["api_key"].endswith("a1b2")

    # Omitted or empty api_key keeps the current key.
    r = c.post("/api/settings", json={"mode": "api", "provider": "openai", "model": "gpt-x", "api_key": ""})
    assert r.status_code == 200 and KEY not in r.text
    assert fake_settings.data["api_key"] == KEY and fake_settings.data["provider"] == "openai"
    r = c.post("/api/settings", json={"mode": "package"})
    assert fake_settings.data["api_key"] == KEY and fake_settings.data["model"] == "gpt-x"
    assert r.json()["mode"] == "package"

    new_key = "sk-new-key-0000000000000000z9y8"
    r = c.post("/api/settings", json={"mode": "api", "provider": "anthropic", "model": "x", "api_key": new_key})
    assert fake_settings.data["api_key"] == new_key and new_key not in r.text

    assert c.post("/api/settings", json={"mode": "magic"}).status_code == 422
    assert c.post("/api/settings", json={"mode": "api", "provider": "nope"}).status_code == 422


def test_settings_scrubs_a_leaky_public(tmp_path, lec_dir, fake_settings):
    fake_settings.public = lambda: {"mode": "api", "api_key": KEY, "note": f"key={KEY}"}
    r = TestClient(_local_app(tmp_path), client=LOCAL).get("/api/settings")
    assert KEY not in r.text and r.json()["api_key"].endswith("a1b2")


def test_settings_test_connection(tmp_path, lec_dir, fake_settings, monkeypatch):
    from rewind import llm

    monkeypatch.setattr(llm, "test_connection",
                        lambda: {"ok": False, "message": f"401 bad key {KEY}", "latency_ms": 12}, raising=False)
    r = TestClient(_local_app(tmp_path), client=LOCAL).post("/api/settings/test")
    assert r.status_code == 200 and r.json()["ok"] is False and KEY not in r.text


def test_missing_modules_return_501(tmp_path, lec_dir, monkeypatch):
    import sys

    from rewind import llm

    # sys.modules[name] = None makes the import raise ModuleNotFoundError for that name.
    monkeypatch.setitem(sys.modules, "rewind.settings", None)
    monkeypatch.setitem(sys.modules, "rewind.context_package", None)
    monkeypatch.delattr(llm, "test_connection", raising=False)
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    for method, url, body in LOCAL_ONLY[:6]:
        r = _call(c, method, url, body)
        assert r.status_code == 501, url
        assert "isn't available yet" in r.json()["detail"]
    assert c.get("/api/ai-status").status_code == 200


def test_ai_status_is_public_and_minimal(tmp_path, lec_dir, fake_settings, monkeypatch):
    from rewind import llm

    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.delattr(llm, "chat", raising=False)
    r = TestClient(_local_app(tmp_path), client=REMOTE).get("/api/ai-status")
    assert r.json() == {"mode": "api", "provider": "openai_compatible", "preset": "openrouter", "model": "m1",
                        "can_generate": True, "chat_available": False,  # no llm.chat / rewind.chat here
                        "coverage_check": False}  # fake mode never runs the coverage check
    assert KEY not in r.text and "openrouter.ai" not in r.text and "api_key" not in r.text


def test_ai_status_derives_preset_when_settings_omit_it(tmp_path, lec_dir, fake_settings, monkeypatch):
    fake_settings.public = lambda: {"mode": "api", "provider": "openai_compatible", "model": "m1",
                                    "api_key": "…a1b2", "base_url": "https://openrouter.ai/api/v1"}
    r = TestClient(_local_app(tmp_path), client=REMOTE).get("/api/ai-status").json()
    assert r["preset"] == "openrouter"
    assert api.preset_for("openai_compatible", "http://localhost:11434/v1") == "ollama"
    assert api.preset_for("openai_compatible", "http://127.0.0.1:1234/v1") == "lmstudio"
    assert api.preset_for("openai_compatible", "https://llm.example.edu/v1") is None
    assert api.preset_for("anthropic", "https://openrouter.ai/api/v1") is None


def test_ai_status_without_settings_module(tmp_path, lec_dir, monkeypatch):
    import sys

    from rewind import llm

    monkeypatch.setitem(sys.modules, "rewind.settings", None)
    monkeypatch.setattr(llm, "available", lambda: False, raising=False)
    r = TestClient(_local_app(tmp_path), client=REMOTE).get("/api/ai-status").json()
    assert set(r) == {"mode", "provider", "preset", "model", "can_generate", "chat_available", "coverage_check"}
    assert r["mode"] == "none" and r["preset"] is None and r["can_generate"] is False


# ------------------------------------------------------------------ generate estimate

def test_generate_estimate_builds_requests_without_calling_ai(tmp_path, lec_dir, monkeypatch):
    from rewind import llm, snippet_maker

    seen = {}

    def estimate(requests):
        seen["requests"] = requests
        return {"input_tokens": 1234, "output_tokens": 5000, "cost_usd": 0.02, "calls": len(requests)}

    def no_ai(*a, **k):
        raise AssertionError("estimate must not call the AI")

    monkeypatch.setattr(llm, "estimate", estimate, raising=False)
    monkeypatch.setattr(llm, "ask_json", no_ai)
    monkeypatch.setattr(snippet_maker, "ask_json", no_ai, raising=False)
    r = TestClient(_local_app(tmp_path), client=LOCAL).get("/api/lectures/demo01/generate/estimate")
    assert r.status_code == 200
    assert r.json() == {"input_tokens": 1234, "output_tokens": 5000, "cost_usd": 0.02, "calls": 1,
                        "scope": "Initial snippet generation; verification, repair and retries are excluded."}
    (req,) = seen["requests"]
    assert set(req) == {"system", "user", "max_tokens", "purpose"}
    assert req["purpose"] == "preprocess"
    assert req["system"] == snippet_maker.SNIPPET_PROMPT
    first = lectures.load_transcript("demo01")[0]
    assert f"[{first['start']:.1f}] {first['text']}" in req["user"]
    assert "Lecture title: Lec 1" in req["user"]
    assert req["max_tokens"] == snippet_maker.MAX_OUTPUT_TOKENS


def test_generate_estimate_errors(tmp_path, lec_dir, monkeypatch):
    from rewind import llm

    monkeypatch.delattr(llm, "estimate", raising=False)
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.get("/api/lectures/demo01/generate/estimate")
    assert r.status_code == 501 and "estimate" in r.json()["detail"]
    monkeypatch.setattr(llm, "estimate", lambda reqs: {"calls": len(reqs)}, raising=False)
    assert c.get("/api/lectures/nope99/generate/estimate").status_code == 404
    (lec_dir / "demo01" / "transcript.json").unlink()
    assert c.get("/api/lectures/demo01/generate/estimate").status_code == 409


def test_package_and_prompt(tmp_path, lec_dir, fake_package):
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.get("/api/lectures/demo01/package")
    assert r.status_code == 200 and r.content == b"PK\x03\x04zip-for-demo01"
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="rewind-demo01-package.zip"' in r.headers["content-disposition"]
    assert c.get("/api/lectures/demo01/prompt").json() == {"parts": ["part 1 for demo01", "part 2"]}
    assert c.get("/api/lectures/nope99/package").status_code == 404
    assert c.get("/api/lectures/..%2Fx/prompt").status_code == 404


def test_import_ok_reindexes(tmp_path, lec_dir, fake_package):
    app = _local_app(tmp_path)
    indexed = []
    app.state.reindex = indexed.append
    r = TestClient(app, client=LOCAL).post("/api/lectures/demo01/import", json={"raw_text": "```json good```"})
    assert r.json() == {"ok": True, "snippets_count": 3, "errors": [], "fix_message": "", "reindexed": True}
    assert fake_package.calls["import"] == ("demo01", "```json good```")
    assert indexed == ["demo01"]


def test_import_errors_do_not_reindex(tmp_path, lec_dir, fake_package):
    app = _local_app(tmp_path)
    indexed = []
    app.state.reindex = indexed.append
    c = TestClient(app, client=LOCAL)
    r = c.post("/api/lectures/demo01/import", json={"raw_text": "broken"}).json()
    assert r["ok"] is False and r["errors"] and r["fix_message"].startswith("Your answer had")
    assert indexed == []
    assert c.post("/api/lectures/demo01/import", json={}).status_code == 422


def test_import_reindex_failure_is_reported(tmp_path, lec_dir, fake_package):
    app = _local_app(tmp_path)

    def boom(lid):
        raise RuntimeError("model missing")

    app.state.reindex = boom
    r = TestClient(app, client=LOCAL).post("/api/lectures/demo01/import", json={"raw_text": "good"}).json()
    assert r["ok"] is True and r["reindexed"] is False and "model missing" in r["reindex_error"]


def _wait_state(c, url, until=("done", "error")):
    import threading

    for _ in range(200):
        s = c.get(url).json()
        if s["state"] in until:
            return s
        threading.Event().wait(0.02)
    return s


def test_generate_runs_in_background(tmp_path, lec_dir, monkeypatch):
    import threading

    from rewind import llm, snippet_maker

    gate = threading.Event()
    fixture = lectures.load_snippets("demo01")

    def make(transcript, lecture_id, title=None, ask=None):
        gate.wait(5)
        return fixture[:2]

    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.setattr(snippet_maker, "make_snippets", make)
    app = _local_app(tmp_path)
    indexed = []
    app.state.reindex = indexed.append
    c = TestClient(app, client=LOCAL)
    url = "/api/lectures/demo01/generate/status"
    assert c.get(url).json() == {"state": "idle", "message": "", "snippets_count": None}

    r = c.post("/api/lectures/demo01/generate").json()
    assert r["ok"] is True and r["state"] == "running"
    assert c.get(url).json()["state"] == "running"
    again = c.post("/api/lectures/demo01/generate").json()
    assert again["ok"] is False and "Already" in again["error"]

    gate.set()
    s = _wait_state(c, url)
    assert s == {"state": "done", "message": "Made 2 snippets.", "snippets_count": 2}
    assert len(lectures.load_snippets("demo01")) == 2
    assert indexed == ["demo01"]


def test_generate_error_keeps_old_snippets(tmp_path, lec_dir, monkeypatch):
    from rewind import llm, snippet_maker

    def make(*a, **k):
        raise RuntimeError(f"401 from provider, key {KEY}")

    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.setattr(snippet_maker, "make_snippets", make)
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    before = lectures.load_snippets("demo01")
    assert c.post("/api/lectures/demo01/generate").json()["ok"] is True
    s = _wait_state(c, "/api/lectures/demo01/generate/status")
    assert s["state"] == "error" and "401" in s["message"] and KEY not in s["message"]
    assert lectures.load_snippets("demo01") == before


def test_generate_refuses_without_llm_or_transcript(tmp_path, lec_dir, monkeypatch):
    from rewind import llm

    monkeypatch.setattr(llm, "available", lambda: False, raising=False)
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.post("/api/lectures/demo01/generate").json()
    assert r["ok"] is False and "AI settings" in r["error"]
    (lec_dir / "demo01" / "transcript.json").unlink()
    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    r = c.post("/api/lectures/demo01/generate").json()
    assert r["ok"] is False and "transcript" in r["error"]
    assert c.post("/api/lectures/nope99/generate").status_code == 404


# ------------------------------------------------------------------ round 5: chat, slides

def _events(resp):
    """Parse a text/event-stream body into the JSON events."""
    out = []
    for block in resp.text.split("\n\n"):
        block = block.strip()
        if block:
            assert block.startswith("data: "), block
            out.append(json.loads(block[len("data: "):]))
    return out


@pytest.fixture
def chat_mod(monkeypatch):
    """A mocked rewind.chat plus llm.available/llm.chat, so chat is available."""
    import sys
    import types

    from rewind import llm

    calls = []
    mod = types.ModuleType("rewind.chat")

    def respond(student_id, snippet_id, messages):
        calls.append({"student_id": student_id, "snippet_id": snippet_id, "messages": messages,
                      "logged_before": mod.history()})
        yield {"type": "text", "text": "He explains this "}
        yield {"type": "text", "text": "at [02:20]."}
        yield {"type": "done", "citations": [{"kind": "time", "lecture_id": "demo01", "t": 140}],
               "outside_materials": mod.outside, "homework": False}

    mod.respond = respond
    mod.calls = calls
    mod.outside = False
    mod.history = lambda: []
    monkeypatch.setitem(sys.modules, "rewind.chat", mod)
    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "x", raising=False)
    return mod


def _chat_body(n=1, last="Why is that?"):
    msgs = []
    for i in range(n - 1):
        msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
    msgs.append({"role": "user", "content": last})
    return {"student_id": "s1", "snippet_id": "demo01-002", "messages": msgs}


def test_chat_streams_events_and_logs_both_messages(tmp_path, lec_dir, chat_mod):
    from rewind import store as store_mod

    db = tmp_path / "chat.db"
    chat_mod.history = lambda: store_mod.chat_history("s1", "demo01-002", db_path=db)
    c = TestClient(api.create_app(fake=True, db_path=db))
    r = c.post("/api/chat", json=_chat_body())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r)
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert events[-1]["citations"] == [{"kind": "time", "lecture_id": "demo01", "t": 140}]
    # The student's message was logged before streaming started.
    (call,) = chat_mod.calls
    assert [(m["role"], m["content"]) for m in call["logged_before"]] == [("user", "Why is that?")]
    history = store_mod.chat_history("s1", "demo01-002", db_path=db)
    assert [(m["role"], m["content"], m["outside_materials"]) for m in history] == [
        ("user", "Why is that?", False), ("assistant", "He explains this at [02:20].", False)]
    stats = c.get("/api/lecturer/stats").json()
    row = next(s for s in stats["snippets"] if s["snippet_id"] == "demo01-002")
    assert row["followups"] == 1 and stats["totals"]["followups"] == 1


def test_chat_outside_materials_is_logged(tmp_path, lec_dir, chat_mod):
    from rewind import store as store_mod

    chat_mod.outside = True
    db = tmp_path / "chat.db"
    TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json=_chat_body())
    assert store_mod.chat_history("s1", "demo01-002", db_path=db)[-1]["outside_materials"] is True


def test_chat_sends_only_the_last_10_messages(tmp_path, lec_dir, chat_mod):
    c = TestClient(_local_app(tmp_path))
    c.post("/api/chat", json=_chat_body(n=15, last="latest?"))
    sent = chat_mod.calls[0]["messages"]
    assert len(sent) == 10 and sent[-1] == {"role": "user", "content": "latest?"}


def test_chat_validation(tmp_path, lec_dir, chat_mod):
    c = TestClient(_local_app(tmp_path))
    body = _chat_body()
    body["messages"].append({"role": "assistant", "content": "hi"})
    assert c.post("/api/chat", json=body).status_code == 400
    assert c.post("/api/chat", json=_chat_body(last="   ")).status_code == 400
    assert c.post("/api/chat", json={**_chat_body(), "snippet_id": "demo01-999"}).status_code == 404
    assert c.post("/api/chat", json={**_chat_body(), "messages": []}).status_code == 422
    bad_role = _chat_body()
    bad_role["messages"][0]["role"] = "system"
    assert c.post("/api/chat", json=bad_role).status_code == 422
    assert chat_mod.calls == []


def test_chat_error_mid_stream_becomes_an_event(tmp_path, lec_dir, chat_mod, monkeypatch):
    from rewind import store as store_mod

    def respond(student_id, snippet_id, messages):
        yield {"type": "text", "text": "Partial"}
        raise RuntimeError(f"provider down, key {KEY}")

    monkeypatch.setenv("LLM_API_KEY", KEY)
    chat_mod.respond = respond
    db = tmp_path / "chat.db"
    r = TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json=_chat_body())
    events = _events(r)
    assert events[0] == {"type": "text", "text": "Partial"}
    assert events[-1]["type"] == "error" and "provider down" in events[-1]["message"]
    assert KEY not in r.text
    assert [m["role"] for m in store_mod.chat_history("s1", "demo01-002", db_path=db)] == ["user"]


def test_chat_409_when_not_available(tmp_path, lec_dir, monkeypatch):
    import sys

    from rewind import llm

    c = TestClient(_local_app(tmp_path))
    monkeypatch.setattr(llm, "available", lambda: False, raising=False)
    r = c.post("/api/chat", json=_chat_body())
    assert r.status_code == 409 and "AI settings" in r.json()["detail"]
    assert c.get("/api/ai-status").json()["chat_available"] is False

    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "x", raising=False)
    monkeypatch.setitem(sys.modules, "rewind.chat", None)
    r = c.post("/api/chat", json=_chat_body())
    assert r.status_code == 409 and "rewind.chat" in r.json()["detail"]

    monkeypatch.delattr(llm, "chat", raising=False)
    assert c.post("/api/chat", json=_chat_body()).status_code == 409


def test_ai_status_chat_available(tmp_path, lec_dir, chat_mod):
    assert TestClient(_local_app(tmp_path), client=REMOTE).get("/api/ai-status").json()["chat_available"] is True


# ---------------------------------------------------------------- slides

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _write_slides(lec_dir, lecture_id="demo01", n=3):
    folder = lec_dir / lecture_id / "slides"
    folder.mkdir(parents=True, exist_ok=True)
    slides = [{"n": i, "title": f"Title {i}", "text": f"Slide text {i}", "image": f"slides/slide-{i:03d}.png"}
              for i in range(1, n + 1)]
    for s in slides:
        (lec_dir / lecture_id / s["image"]).write_bytes(PNG)
    (folder / "slides.json").write_text(json.dumps(slides), encoding="utf-8")
    return slides


@pytest.fixture
def slides_mod(monkeypatch, lec_dir):
    """A mocked rewind.slides that writes slides.json and aligns snippet 2 to slides 1-2."""
    import sys
    import types

    mod = types.ModuleType("rewind.slides")
    mod.calls = []

    def ingest_slides(lecture_id, path):
        mod.calls.append(("ingest", lecture_id, Path(path)))
        return len(_write_slides(lec_dir, lecture_id))

    def align(lecture_id):
        mod.calls.append(("align", lecture_id))
        snippets = lectures.load_snippets(lecture_id)
        for s in snippets:
            s["slides"] = [1, 2] if s["id"].endswith("-002") else []
        lectures.save_snippets(lecture_id, snippets)

    def load_slides(lecture_id):
        path = lec_dir / lecture_id / "slides" / "slides.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    mod.ingest_slides, mod.align, mod.load_slides = ingest_slides, align, load_slides
    monkeypatch.setitem(sys.modules, "rewind.slides", mod)
    return mod


def test_upload_slides_saves_source_ingests_aligns_and_reindexes(tmp_path, lec_dir, slides_mod):
    from pathlib import Path as P

    app = _local_app(tmp_path)
    indexed = []
    app.state.reindex = indexed.append
    c = TestClient(app, client=LOCAL)
    r = c.post("/api/lectures/demo01/slides",
               files={"file": ("../../Lecture 1 slides.PDF", b"%PDF-1.4 fake", "application/pdf")})
    assert r.status_code == 200, r.text
    assert r.json() == {"count": 3, "aligned": True}
    saved = lec_dir / "demo01" / "source" / "Lecture-1-slides.pdf"
    assert saved.read_bytes() == b"%PDF-1.4 fake"
    assert slides_mod.calls == [("ingest", "demo01", saved), ("align", "demo01")]
    assert indexed == ["demo01"]
    assert lectures.get_snippet("demo01-002")["slides"] == [1, 2]
    assert not list(P(tmp_path).glob("*.PDF"))  # nothing written outside the lecture folder


def test_upload_slides_errors(tmp_path, lec_dir, slides_mod, monkeypatch):
    import sys

    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.post("/api/lectures/demo01/slides", files={"file": ("notes.docx", b"x", "application/octet-stream")})
    assert r.status_code == 400 and "Unsupported" in r.json()["detail"]
    assert c.post("/api/lectures/demo01/slides", files={"file": ("s.pdf", b"", "application/pdf")}).status_code == 400
    assert c.post("/api/lectures/demo01/slides", data={"x": "1"}).status_code == 400
    assert c.post("/api/lectures/nope99/slides", files={"file": ("s.pdf", b"x", "application/pdf")}).status_code == 404

    def broken(lecture_id, path):
        raise ValueError("not a PDF")

    slides_mod.ingest_slides = broken
    r = c.post("/api/lectures/demo01/slides", files={"file": ("s.pdf", b"junk", "application/pdf")})
    assert r.status_code == 422 and "not a PDF" in r.json()["detail"]

    monkeypatch.setitem(sys.modules, "rewind.slides", None)
    r = c.post("/api/lectures/demo01/slides", files={"file": ("s.pdf", b"x", "application/pdf")})
    assert r.status_code == 501


def test_upload_slides_without_snippets_is_not_aligned(tmp_path, lec_dir, slides_mod):
    (lec_dir / "demo01" / "snippets.json").write_text("[]", encoding="utf-8")
    app = _local_app(tmp_path)
    app.state.reindex = lambda lid: None
    r = TestClient(app, client=LOCAL).post("/api/lectures/demo01/slides",
                                          files={"file": ("s.pptx", b"PK", "application/octet-stream")})
    assert r.json() == {"count": 3, "aligned": False}
    assert [c[0] for c in slides_mod.calls] == ["ingest"]


def test_upload_slides_is_local_only_and_never_reads_the_body(tmp_path, lec_dir, slides_mod):
    r = TestClient(_local_app(tmp_path), client=REMOTE).post(
        "/api/lectures/demo01/slides", files={"file": ("s.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 403
    assert slides_mod.calls == [] and not (lec_dir / "demo01" / "source").exists()


def test_get_slides_public(tmp_path, lec_dir):
    c = TestClient(_local_app(tmp_path), client=REMOTE)
    assert c.get("/api/lectures/demo01/slides").json() == []
    _write_slides(lec_dir)
    slides = c.get("/api/lectures/demo01/slides").json()
    assert [s["n"] for s in slides] == [1, 2, 3]
    assert slides[0] == {"n": 1, "title": "Title 1", "text": "Slide text 1", "image": "slides/slide-001.png",
                         "image_url": "media/demo01/slides/slide-001.png"}
    assert c.get("/api/lectures/nope99/slides").status_code == 404
    _copy_demo(lec_dir / "hid01", hidden=True, lecture_id="hid01")
    _write_slides(lec_dir, "hid01")
    assert c.get("/api/lectures/hid01/slides").status_code == 404


def test_slide_media_and_traversal(tmp_path, lec_dir):
    _write_slides(lec_dir)
    (lec_dir / "demo01" / "slides" / "notes.txt").write_text("secret", encoding="utf-8")
    c = TestClient(_local_app(tmp_path), client=REMOTE)
    r = c.get("/media/demo01/slides/slide-001.png")
    assert r.status_code == 200 and r.content == PNG
    for bad in ("/media/demo01/slides/notes.txt", "/media/demo01/slides/..%2Fmeta.json",
                "/media/demo01/slides/..%2F..%2Fdemo01%2Fmeta.json", "/media/demo01/slides/missing.png",
                "/media/..%2Fdemo01/slides/slide-001.png", "/media/demo01/slides/slides.json"):
        assert c.get(bad).status_code == 404, bad


def test_slide_image_url_rejects_odd_paths():
    assert api.slide_image_url("x", {"image": "slides/s-1.png"}) == "media/x/slides/s-1.png"
    assert api.slide_image_url("x", {"image": "slides\\s-1.png"}) == "media/x/slides/s-1.png"
    for image in ("../s.png", "slides/../../s.png", "other/s.png", "s.png", "", None):
        assert api.slide_image_url("x", {"image": image}) is None


def test_ask_found_includes_clip_and_search_slides(client, lec_dir, monkeypatch):
    from tests import fakes

    _write_slides(lec_dir)
    best = dict(lectures.get_snippet("demo01-002"), slides=[1])
    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "found", "best": best, "alternatives": [], "score": 0.8,
        "slides": [{"lecture_id": "demo01", "n": 3, "score": 0.61}, {"lecture_id": "demo01", "n": 1, "score": 0.5},
                   {"lecture_id": "demo01", "n": 99, "score": 0.5}]})
    r = client.post("/api/ask", json={"student_id": "s1", "question": "x"}).json()
    assert r["status"] == "found"
    assert r["snippet"]["slides"] == [1]
    assert r["slides"] == [
        {"lecture_id": "demo01", "n": 1, "title": "Title 1", "image_url": "media/demo01/slides/slide-001.png",
         "score": None, "source": "clip"},
        {"lecture_id": "demo01", "n": 3, "title": "Title 3", "image_url": "media/demo01/slides/slide-003.png",
         "score": 0.61, "source": "search"},
    ]


def test_ask_slide_only(client, lec_dir, monkeypatch):
    from tests import fakes

    _write_slides(lec_dir)
    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "slide_only", "best": None, "alternatives": [], "score": 0.3,
        "slides": [{"lecture_id": "demo01", "n": 2, "score": 0.55}]})
    r = client.post("/api/ask", json={"student_id": "s1", "question": "What is on slide two?"}).json()
    assert r["status"] == "slide_only" and r["snippet"] is None and r["alternatives"] == []
    assert r["lecture"]["lecture_id"] == "demo01"
    assert [s["n"] for s in r["slides"]] == [2] and r["slides"][0]["source"] == "search"
    assert client.get("/api/lecturer/gaps").json() == []  # covered by the slides: not a gap
    totals = client.get("/api/lecturer/stats").json()["totals"]
    assert totals["asks"] == 1 and totals["slide_only_pct"] == 100.0


def test_ask_slide_only_in_hidden_lecture_is_not_covered(client, lec_dir, monkeypatch):
    from tests import fakes

    _copy_demo(lec_dir / "hid01", hidden=True, lecture_id="hid01")
    _write_slides(lec_dir, "hid01")
    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "slide_only", "best": None, "alternatives": [], "score": 0.3,
        "slides": [{"lecture_id": "hid01", "n": 2, "score": 0.55}]})
    r = client.post("/api/ask", json={"student_id": "s1", "question": "q"}).json()
    assert r["status"] == "not_covered" and r["slides"] == []
    assert len(client.get("/api/lecturer/gaps").json()) == 1


def test_generate_uses_preprocess_purpose_and_realigns(tmp_path, lec_dir, slides_mod, monkeypatch):
    from rewind import llm, snippet_maker

    seen = {}
    fixture = lectures.load_snippets("demo01")

    def make(transcript, lecture_id, title=None, ask=None, purpose=None):
        seen["purpose"] = purpose
        return fixture

    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    monkeypatch.setattr(snippet_maker, "make_snippets", make)
    _write_slides(lec_dir)
    app = _local_app(tmp_path)
    app.state.reindex = lambda lid: None
    c = TestClient(app, client=LOCAL)
    c.post("/api/lectures/demo01/generate")
    assert _wait_state(c, "/api/lectures/demo01/generate/status")["state"] == "done"
    assert seen["purpose"] == "preprocess"
    assert ("align", "demo01") in slides_mod.calls
    assert lectures.get_snippet("demo01-002")["slides"] == [1, 2]


def test_estimate_asks_for_preprocess_pricing(tmp_path, lec_dir, monkeypatch):
    from rewind import llm

    seen = {}

    def estimate(requests, purpose="runtime"):
        seen["purpose"] = purpose
        return {"calls": len(requests), "model": "sonnet" if purpose == "preprocess" else "haiku"}

    monkeypatch.setattr(llm, "estimate", estimate, raising=False)
    r = TestClient(_local_app(tmp_path), client=LOCAL).get("/api/lectures/demo01/generate/estimate").json()
    assert seen["purpose"] == "preprocess" and r["model"] == "sonnet" and r["calls"] == 1


@pytest.mark.parametrize("mode", ["none", "package"])
def test_real_settings_disable_chat(tmp_path, lec_dir, monkeypatch, mode):
    from rewind import settings

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    settings.save({"mode": mode})
    c = TestClient(_local_app(tmp_path))
    assert c.get("/api/ai-status").json()["chat_available"] is False
    assert c.post("/api/chat", json=_chat_body()).status_code == 409


def test_real_settings_policy_preprocess_and_validation(tmp_path, lec_dir, monkeypatch):
    from rewind import settings

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    c = TestClient(_local_app(tmp_path), client=LOCAL)
    r = c.post("/api/settings", json={"mode": "none", "chat_policy": "labelled",
                                      "preprocess_model": "preprocess-only"})
    assert r.status_code == 200
    assert r.json()["chat_policy"] == settings.load()["chat_policy"] == "labelled"
    assert r.json()["preprocess_model"] == "preprocess-only"
    assert c.post("/api/settings", json={"mode": "none", "chat_policy": "invent"}).status_code == 422
    r = c.post("/api/settings", json={"mode": "none", "base_url": "https://user:secret@example.com"})
    assert r.status_code == 422
    assert settings.load()["chat_policy"] == "labelled"


@pytest.mark.parametrize("tail", [[], [{"type": "unexpected"}], [None],
                                  [{"type": "text", "text": 42}],
                                  [{"type": "done", "citations": [], "outside_materials": "false", "homework": False}],
                                  [{"type": "error", "message": "Provider unavailable"}]])
def test_chat_incomplete_or_invalid_does_not_save_assistant(tmp_path, lec_dir, chat_mod, tail):
    from rewind import store

    closed = []

    def respond(*args):
        try:
            yield {"type": "text", "text": "Partial"}
            yield from tail
        finally:
            closed.append(True)

    chat_mod.respond = respond
    db = tmp_path / "chat.db"
    r = TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json=_chat_body())
    assert _events(r)[-1]["type"] == "error"
    assert [m["role"] for m in store.chat_history("s1", "demo01-002", db)] == ["user"]
    assert closed == [True]


def test_chat_provider_fails_before_first_token_once(tmp_path, lec_dir, chat_mod):
    from rewind import store

    calls = []

    def respond(*args):
        calls.append(True)
        raise TypeError("provider failed")

    chat_mod.respond = respond
    db = tmp_path / "chat.db"
    r = TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json=_chat_body())
    assert [e["type"] for e in _events(r)] == ["error"]
    assert calls == [True]
    assert len(store.chat_history("s1", "demo01-002", db)) == 1


@pytest.mark.parametrize("disconnect", [False, True])
def test_chat_disconnect_closes_source_without_completed_history(tmp_path, lec_dir, chat_mod, disconnect):
    import asyncio
    from starlette.requests import Request
    from rewind import store

    closed = []

    def respond(*args):
        try:
            yield {"type": "text", "text": "Partial"}
            yield {"type": "done", "citations": [], "outside_materials": False, "homework": False}
        finally:
            closed.append(True)

    chat_mod.respond = respond
    db = tmp_path / "chat.db"
    app = api.create_app(fake=True, db_path=db)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/chat")
    request = Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": []})

    async def connected():
        return False

    request.is_disconnected = connected
    response = endpoint(api.ChatBody(**_chat_body()), request)

    async def run():
        assert "Partial" in await anext(response.body_iterator)
        if disconnect:
            async def disconnected():
                return True
            request.is_disconnected = disconnected
            assert [item async for item in response.body_iterator] == []
        else:
            await response.body_iterator.aclose()  # cancellation while suspended at a yield

    asyncio.run(run())
    assert closed == [True]
    assert [m["role"] for m in store.chat_history("s1", "demo01-002", db)] == ["user"]


def test_chat_with_real_chat_and_llm_stream_boundary(tmp_path, lec_dir, monkeypatch):
    from rewind import llm, settings, store
    import rewind.chat  # exercise the actual chat module with a fake provider stream

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    settings.save({"mode": "api", "provider": "openai_compatible", "model": "offline-model",
                   "api_key": "offline-key", "base_url": "http://localhost:11434/v1"})
    seen = []

    def fake_chat(system, messages, max_tokens=800, stream=False, purpose="runtime"):
        seen.append((stream, purpose))
        return iter(['The lecture explains the matrix at [02:', '20].\n',
                     '<<META {"outside":false,"homework":false} META>>'])

    monkeypatch.setattr(llm, "chat", fake_chat)
    db = tmp_path / "chat.db"
    r = TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json=_chat_body())
    assert _events(r)[-1]["type"] == "done"
    assert seen == [(True, "runtime")]
    assert "META" not in r.text
    assert [m["role"] for m in store.chat_history("s1", "demo01-002", db)] == ["user", "assistant"]


def test_real_estimate_uses_preprocess_without_provider_call(tmp_path, lec_dir, monkeypatch):
    from rewind import llm, settings

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    settings.save({"model": "runtime", "preprocess_model": "preprocess"})
    def no_call(*args, **kwargs):
        raise AssertionError("No live provider calls")
    monkeypatch.setattr(llm, "ask_json", no_call)
    r = TestClient(_local_app(tmp_path), client=LOCAL).get("/api/lectures/demo01/generate/estimate")
    assert r.status_code == 200
    assert r.json()["model"] == "preprocess" and r.json()["approximate"] is True
    assert r.json()["cost_usd"] is None
    assert all(item["purpose"] == "preprocess" for item in r.json()["requests"])


@pytest.mark.parametrize("size", [33, 70000])
def test_slide_upload_size_limits_before_ingest(tmp_path, lec_dir, slides_mod, monkeypatch, size):
    monkeypatch.setattr(api, "MAX_SLIDES_BYTES", 32)
    r = TestClient(_local_app(tmp_path), client=LOCAL).post(
        "/api/lectures/demo01/slides", files={"file": ("s.pdf", b"x" * size, "application/pdf")})
    assert r.status_code == 413
    assert slides_mod.calls == []
    assert not (lec_dir / "demo01" / "source").exists()


def test_upload_closes_form_on_failure(tmp_path, lec_dir, slides_mod, monkeypatch):
    from starlette.datastructures import UploadFile

    closed = []
    original = UploadFile.close
    async def close(self):
        closed.append(self.filename)
        await original(self)
    monkeypatch.setattr(UploadFile, "close", close)
    r = TestClient(_local_app(tmp_path), client=LOCAL).post(
        "/api/lectures/demo01/slides", files={"file": ("notes.exe", b"x")})
    assert r.status_code == 400 and closed == ["notes.exe"]


def test_multiple_secrets_are_scrubbed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "different-env-secret")
    result = api.scrub_secret({"error": ["saved-secret and different-env-secret"]}, "saved-secret")
    assert "saved-secret" not in str(result) and "different-env-secret" not in str(result)


def test_real_pdf_upload_alignment_media_and_slide_only(tmp_path, lec_dir, monkeypatch):
    import pymupdf
    import numpy as np
    from rewind import search
    from tests import fakes

    # Exercise real extraction/alignment but no model download or provider calls.
    monkeypatch.setattr(search, "_encode", lambda texts: np.ones((len(texts), 1), dtype=np.float32))
    document = pymupdf.open()
    for title in ("Demand", "Supply", "Equilibrium"):
        page = document.new_page()
        page.insert_text((72, 72), title + "\nA temporary economics slide.")
    source = document.tobytes()
    document.close()
    app = _local_app(tmp_path)
    reindexed = []
    app.state.reindex = lambda lid: reindexed.append(lid)
    c = TestClient(app, client=LOCAL)
    r = c.post("/api/lectures/demo01/slides", files={"file": ("../../../deck.pdf", source, "application/pdf")})
    assert r.status_code == 200 and r.json() == {"count": 3, "aligned": True}
    assert reindexed == ["demo01"]
    assert (lec_dir / "demo01/source/deck.pdf").read_bytes() == source
    pages = c.get("/api/lectures/demo01/slides").json()
    assert [p["n"] for p in pages] == [1, 2, 3]
    assert pages[0]["text"].startswith("Demand")
    image = c.get("/" + pages[0]["image_url"])
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    assert all(s["slides"] == [1, 2] for s in lectures.load_snippets("demo01"))
    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "slide_only", "best": None, "alternatives": [], "score": 0.8,
        "slides": [{"lecture_id": "demo01", "n": 3, "score": 0.8}]})
    result = c.post("/api/ask", json={"student_id": "pdf-test", "question": "Equilibrium?"}).json()
    assert result["status"] == "slide_only"
    assert result["slides"][0]["title"] == "Equilibrium"
    assert c.get("/api/lecturer/gaps").json() == []


def test_missing_slide_preview_retains_readable_content(client, lec_dir, monkeypatch):
    from tests import fakes

    _write_slides(lec_dir)
    (lec_dir / "demo01/slides/slide-002.png").unlink()
    monkeypatch.setattr(fakes.search, "answer", lambda q: {
        "status": "slide_only", "best": None, "alternatives": [], "score": 0.8,
        "slides": [{"lecture_id": "demo01", "n": 2, "score": 0.8}]})
    r = client.post("/api/ask", json={"student_id": "s1", "question": "Explain this slide?"})
    assert r.json()["status"] == "slide_only"
    slide = r.json()["slides"][0]
    assert slide["image_url"] is None and slide["text"] == "Slide text 2"


def test_chat_asgi_disconnect_cleans_up_without_success(tmp_path, lec_dir, chat_mod):
    import asyncio
    from rewind import store

    closed = []
    def respond(*args):
        try:
            yield {"type": "text", "text": "Partial"}
            yield {"type": "done", "citations": [], "outside_materials": False, "homework": False}
        finally:
            closed.append(True)
    chat_mod.respond = respond
    db = tmp_path / "asgi.db"
    app = api.create_app(fake=True, db_path=db)
    sent = []

    async def run():
        body = json.dumps(_chat_body()).encode()
        first = True
        disconnected = asyncio.Event()
        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def send(message):
            sent.append(message)
            if b"Partial" in message.get("body", b""):
                disconnected.set()
                await asyncio.sleep(0)  # allow the response's disconnect listener to cancel
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/api/chat",
                 "raw_path": b"/api/chat", "root_path": "", "query_string": b"",
                 "headers": [(b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
        await app(scope, receive, send)
    asyncio.run(run())
    assert closed == [True]
    assert b'"type": "done"' not in b"".join(message.get("body", b"") for message in sent)
    assert [m["role"] for m in store.chat_history("s1", "demo01-002", db)] == ["user"]


def test_public_search_failure_masks_provider_key(client, monkeypatch):
    from tests import fakes

    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    def fail(question):
        raise RuntimeError("Provider failed " + KEY)
    monkeypatch.setattr(fakes.search, "answer", fail)
    r = client.post("/api/ask", json={"student_id": "s1", "question": "Why?"})
    assert r.status_code == 503 and KEY not in r.text


@pytest.mark.parametrize("updates", [{"student_id": ""}, {"student_id": " "},
                                     {"messages": "bad"}, {"messages": [{"role": "user", "content": "x" * 4001}]}])
def test_chat_rejects_malformed_request_without_logging(tmp_path, lec_dir, chat_mod, updates):
    from rewind import store

    db = tmp_path / "bad-chat.db"
    r = TestClient(api.create_app(fake=True, db_path=db)).post("/api/chat", json={**_chat_body(), **updates})
    assert r.status_code in (400, 422) and chat_mod.calls == []
    assert store.chat_history("s1", "demo01-002", db) == []
