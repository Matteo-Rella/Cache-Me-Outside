"""Tests for rewind.ingest with a tiny dummy video, mocked transcriber, LLM and indexer."""
from __future__ import annotations

import json
import sys
import types

import pytest

from rewind import config, ingest, lectures

DEMO_DIR = config.LECTURES_DIR / "demo01"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Lectures dir in tmp, a dummy video, the demo01 transcript and raw snippets as LLM output."""
    lec_dir = tmp_path / "lectures"
    lec_dir.mkdir()
    monkeypatch.setattr(config, "LECTURES_DIR", lec_dir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    from rewind import llm

    monkeypatch.delattr(llm, "available", raising=False)
    video = tmp_path / "My Lecture.MP4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    transcript = json.loads((DEMO_DIR / "transcript.json").read_text(encoding="utf-8"))
    raw = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")}
           for s in json.loads((DEMO_DIR / "snippets.json").read_text(encoding="utf-8"))]
    return types.SimpleNamespace(tmp=tmp_path, lec_dir=lec_dir, video=video, transcript=transcript, raw=raw)


def _args(env, *extra):
    return ingest.build_parser().parse_args(
        ["--video", str(env.video), "--lecture-id", "ai01", "--title", "Intro to AI",
         "--course", "AI 101", "--lecture-number", "1", *extra])


def _indexer(calls):
    return lambda lecture_id: calls.append(lecture_id)


def test_full_run_with_whisper_and_llm(env, capsys):
    llm_calls, indexed = [], []

    def ask(system, user, **kw):
        llm_calls.append(user)
        return env.raw

    rc = ingest.run(_args(env), transcriber=lambda video: env.transcript, ask=ask, indexer=_indexer(indexed))
    assert rc == 0
    folder = env.lec_dir / "ai01"
    assert (folder / "video.mp4").read_bytes() == env.video.read_bytes()
    meta = lectures.load_meta("ai01")
    assert meta == {"lecture_id": "ai01", "title": "Intro to AI", "course": "AI 101",
                    "lecture_number": 1, "video_url": "media/ai01/video.mp4"}
    assert lectures.load_transcript("ai01") == env.transcript
    snippets = lectures.load_snippets("ai01")
    assert [s["id"] for s in snippets] == ["ai01-001", "ai01-002", "ai01-003"]
    assert all(s["lecture_id"] == "ai01" and s["text"] for s in snippets)
    assert "Lecture title: Intro to AI" in llm_calls[0]
    assert indexed == ["ai01"]
    out = capsys.readouterr().out
    assert "segments  " in out and "snippets  3" in out and "http://localhost:8000/" in out


def test_transcript_file_is_validated_and_copied(env):
    path = env.tmp / "t.json"
    path.write_text(json.dumps(env.transcript), encoding="utf-8")

    def no_whisper(video):
        raise AssertionError("must not transcribe when --transcript is given")

    rc = ingest.run(_args(env, "--transcript", str(path)), transcriber=no_whisper,
                    ask=lambda *a, **k: env.raw, indexer=_indexer([]))
    assert rc == 0
    assert lectures.load_transcript("ai01") == env.transcript


def test_bad_transcript_stops(env):
    path = env.tmp / "bad.json"
    path.write_text(json.dumps([{"start": 5, "end": 2, "text": "x"}]), encoding="utf-8")
    with pytest.raises(ingest.IngestError, match="contract"):
        ingest.run(_args(env, "--transcript", str(path)), indexer=_indexer([]))


def test_no_whisper_and_no_transcript_stops_clearly(env, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import -> ImportError
    monkeypatch.setattr(sys, "argv", ["x"])
    rc = ingest.main(["--video", str(env.video), "--lecture-id", "ai01"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "faster-whisper" in err and "--transcript" in err


def test_whisper_path_uses_faster_whisper(env, monkeypatch, capsys):
    seg = lambda s, e, t: types.SimpleNamespace(start=s, end=e, text=t)  # noqa: E731
    created = {}

    class FakeModel:
        def __init__(self, name, device, compute_type):
            created.update(name=name, device=device)

        def transcribe(self, path, **kw):
            info = types.SimpleNamespace(duration=10.0, language="en")
            return iter([seg(0.0, 4.0, " Hello class."), seg(4.0, 10.0, " Today: search.")]), info

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    segments = ingest.transcribe_whisper(env.video)
    assert created == {"name": "small", "device": "auto"}
    assert segments == [{"start": 0.0, "end": 4.0, "text": "Hello class."},
                        {"start": 4.0, "end": 10.0, "text": "Today: search."}]
    assert "100%" in capsys.readouterr().out


def test_raw_import_without_llm(env):
    raw_path = env.tmp / "raw.json"
    raw_path.write_text("```json\n" + json.dumps({"snippets": env.raw}) + "\n```", encoding="utf-8")
    indexed = []
    rc = ingest.run(_args(env, "--raw", str(raw_path)), transcriber=lambda v: env.transcript,
                    indexer=_indexer(indexed))
    assert rc == 0
    assert len(lectures.load_snippets("ai01")) == 3
    assert indexed == ["ai01"]


def test_raw_import_prefers_snippet_maker_import_raw(env, monkeypatch):
    from rewind import snippet_maker

    seen = {}

    def import_raw(path, segments, lecture_id):
        seen["args"] = (str(path), len(segments), lecture_id)
        return snippet_maker.finalize(env.raw, segments, lecture_id)

    monkeypatch.setattr(snippet_maker, "import_raw", import_raw, raising=False)
    raw_path = env.tmp / "raw.json"
    raw_path.write_text("[]", encoding="utf-8")
    ingest.run(_args(env, "--raw", str(raw_path)), transcriber=lambda v: env.transcript, indexer=_indexer([]))
    assert seen["args"] == (str(raw_path), len(env.transcript), "ai01")


def test_no_llm_no_raw_exports_prompt(env, capsys):
    indexed = []
    rc = ingest.run(_args(env), transcriber=lambda v: env.transcript, indexer=_indexer(indexed))
    assert rc == 2
    assert indexed == []
    assert not (env.lec_dir / "ai01" / "snippets.json").exists()
    prompt = (env.lec_dir / "ai01" / ingest.PROMPT_FILE).read_text(encoding="utf-8")
    from rewind import snippet_maker

    assert snippet_maker.SNIPPET_PROMPT.strip()[:200] in prompt
    assert f"[{env.transcript[0]['start']:.1f}]" in prompt
    out = capsys.readouterr().out
    assert "--raw raw.json" in out and "--transcript" in out


def test_llm_available_uses_llm_module_when_present(env, monkeypatch):
    from rewind import llm

    assert ingest.llm_available() is False
    monkeypatch.setattr(llm, "available", lambda: True, raising=False)
    assert ingest.llm_available() is True
    monkeypatch.setattr(llm, "available", lambda: False, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_MODEL", "m")
    assert ingest.llm_available() is False


def test_guards(env):
    with pytest.raises(ingest.IngestError, match="lecture-id"):
        ingest.run(ingest.build_parser().parse_args(["--video", str(env.video), "--lecture-id", "../x"]))
    with pytest.raises(ingest.IngestError, match="not found"):
        ingest.run(ingest.build_parser().parse_args(["--video", str(env.tmp / "nope.mp4"), "--lecture-id", "a"]))
    txt = env.tmp / "notes.txt"
    txt.write_text("x")
    with pytest.raises(ingest.IngestError, match="Unsupported"):
        ingest.run(ingest.build_parser().parse_args(["--video", str(txt), "--lecture-id", "a"]))

    ingest.run(_args(env), transcriber=lambda v: env.transcript, ask=lambda *a, **k: env.raw, indexer=_indexer([]))
    with pytest.raises(ingest.IngestError, match="--force"):
        ingest.run(_args(env), transcriber=lambda v: env.transcript, indexer=_indexer([]))
    assert ingest.run(_args(env, "--force"), transcriber=lambda v: env.transcript,
                      ask=lambda *a, **k: env.raw, indexer=_indexer([])) == 0
