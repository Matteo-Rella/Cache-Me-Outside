"""Tests for rewind.snippet_maker with the LLM mocked, using the demo01 fixture."""
from __future__ import annotations

import copy
import json
import logging
import re

import pytest

from rewind import config, lectures
from rewind import snippet_maker as sm
from rewind.llm import parse_json


@pytest.fixture
def transcript():
    return lectures.load_transcript("demo01")


@pytest.fixture
def fixture_snippets():
    return lectures.load_snippets("demo01")


def q(answer=1, options=None, **extra):
    return {
        "q": "Which option applies the idea?",
        "options": options if options is not None else ["a", "b", "c", "d"],
        "answer": answer,
        "explanation": "Because the professor said so.",
        **extra,
    }


def raw(concept, start, end, questions=None):
    return {
        "concept": concept,
        "start": start,
        "end": end,
        "summary": "First sentence. Second sentence.",
        "questions": questions if questions is not None else [q(), q(2)],
    }


def fake_ask(response):
    """Fake ask_json that records its calls and returns `response` (or response(user))."""
    calls = []

    def ask(system, user, max_tokens=None):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return copy.deepcopy(response(user) if callable(response) else response)

    ask.calls = calls
    return ask


# ---------------------------------------------------------------- formatting


def test_format_transcript_shows_times(transcript):
    lines = sm.format_transcript(transcript).splitlines()
    assert len(lines) == len(transcript)
    assert lines[0] == f"[{transcript[0]['start']:.1f}] {transcript[0]['text']}"


def test_short_transcript_is_one_window(transcript):
    assert sm.split_windows(transcript) == [transcript]


def test_long_transcript_splits_into_overlapping_windows(transcript):
    windows = sm.split_windows(transcript, max_chars=1500, overlap_chars=300)
    assert len(windows) > 1
    assert all(sum(len(sm.format_transcript([s])) + 1 for s in w) <= 1500 for w in windows)
    seen = {s["start"] for w in windows for s in w}
    assert seen == {s["start"] for s in transcript}  # nothing lost
    for a, b in zip(windows, windows[1:]):
        assert b[0]["start"] < a[-1]["end"]  # consecutive windows overlap
        assert b[0]["start"] > a[0]["start"]  # and always move forward


# ---------------------------------------------------------------- snapping


def test_snap_picks_nearest_boundary():
    b = [0.0, 10.0, 20.0]
    assert sm.snap(4.9, b) == 0.0
    assert sm.snap(5.0, b) == 0.0  # tie -> earlier
    assert sm.snap(5.1, b) == 10.0
    assert sm.snap(-3, b) == 0.0
    assert sm.snap(99, b) == 20.0


def test_snap_snippet_uses_segment_starts_and_ends(transcript):
    s = sm.snap_snippet(raw("x", 63.4, 136.9), transcript)
    starts = {seg["start"] for seg in transcript}
    ends = {seg["end"] for seg in transcript}
    assert s["start"] == 62.68 and s["start"] in starts
    assert s["end"] == 137.49 and s["end"] in ends


def test_snap_clamps_to_transcript_range(transcript):
    s = sm.snap_snippet(raw("x", -50, 9999), transcript)
    assert s["start"] == transcript[0]["start"]
    assert s["end"] == transcript[-1]["end"]


# ---------------------------------------------------------------- overlaps


def test_remove_overlaps_sorts_and_trims_small_overlap(transcript):
    a = sm.snap_snippet(raw("A", 62.68, 137.49), transcript)
    b = sm.snap_snippet(raw("B", 128.97, 212.94), transcript)  # overlaps A by ~8 s
    out = sm.remove_overlaps([b, a], transcript)
    assert [s["concept"] for s in out] == ["A", "B"]
    assert out[1]["start"] >= out[0]["end"]
    assert out[1]["start"] == 137.49  # first segment starting at/after A's end


def test_remove_overlaps_drops_duplicates(transcript, caplog):
    a = raw("A", 62.68, 137.49)
    dup = raw("A again", 65.53, 133.79)  # mostly inside A, e.g. from an overlapping window
    with caplog.at_level(logging.WARNING):
        out = sm.remove_overlaps([a, dup], transcript)
    assert [s["concept"] for s in out] == ["A"]
    assert "duplicates" in caplog.text


def test_remove_overlaps_drops_short_snippets(transcript):
    out = sm.remove_overlaps([raw("tiny", 62.68, 73.88), raw("ok", 73.88, 137.49)], transcript)
    assert [s["concept"] for s in out] == ["ok"]


def test_remove_overlaps_drops_snippet_that_becomes_too_short_after_trim(transcript):
    a = raw("A", 62.68, 137.49)
    b = raw("B", 125.44, 150.43)  # overlap 12 s < half of 25 s; trimmed to 13 s -> dropped
    out = sm.remove_overlaps([a, b], transcript)
    assert [s["concept"] for s in out] == ["A"]


# ---------------------------------------------------------------- validation


def test_valid_question_passes_through():
    assert sm.validate_question(q(3)) == q(3)


@pytest.mark.parametrize(
    "bad",
    [
        q(options=["a", "b", "c"]),  # 3 options
        q(options=["a", "b", "c", "d", "e"]),  # 5 options
        q(options=["a", "b", "", "d"]),  # empty option
        q(options=["a", "a", "c", "d"]),  # duplicate options
        q(answer=4),  # out of range
        q(answer=-1),
        q(answer=None),
        q(answer=True),  # bool is not an index
        q(answer="E"),
        {**q(), "q": ""},
        {**q(), "explanation": ""},
        "not a dict",
    ],
)
def test_invalid_questions_are_rejected(bad):
    assert sm.validate_question(bad) is None


@pytest.mark.parametrize("answer, expected", [("2", 2), ("B", 1), ("d", 3), (1.0, 1), ("c", 2)])
def test_answer_forms_are_normalised(answer, expected):
    assert sm.validate_question(q(answer))["answer"] == expected


def test_answer_given_as_option_text():
    assert sm.validate_question(q("third", options=["first", "second", "third", "fourth"]))["answer"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {**raw("x", 10, 80), "concept": ""},
        {**raw("x", 10, 80), "summary": "  "},
        raw("x", 80, 10),  # end before start
        raw("x", "soon", 80),
        {k: v for k, v in raw("x", 10, 80).items() if k != "end"},
        ["not", "a", "dict"],
    ],
)
def test_invalid_snippets_are_rejected(bad):
    assert sm.validate_snippet(bad) is None


def test_validate_snippet_keeps_only_valid_questions(caplog):
    with caplog.at_level(logging.WARNING):
        s = sm.validate_snippet(raw("x", 10, 80, [q(), q(answer=9)]))
    assert len(s["questions"]) == 1
    assert "1 valid question" in caplog.text


# ---------------------------------------------------------------- end to end (LLM mocked)


def _without_option_order(snippets):
    """Snippets with each question reduced to (q, set of options, correct option text, explanation)."""
    out = []
    for s in snippets:
        s = dict(s)
        s["questions"] = [(q["q"], frozenset(q["options"]), q["options"][q["answer"]], q["explanation"])
                          for q in s["questions"]]
        out.append(s)
    return out


def test_make_snippets_reproduces_fixture(transcript, fixture_snippets):
    """LLM output with the fixture's times and content -> the fixture snippets (options may be reordered)."""
    llm_out = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    ask = fake_ask(llm_out)
    out = sm.make_snippets(transcript, "demo01", ask=ask, verify=False)
    assert _without_option_order(out) == _without_option_order(fixture_snippets)
    assert len(ask.calls) == 1
    assert ask.calls[0]["system"] == sm.SNIPPET_PROMPT
    assert "[62.7] This is my plan." in ask.calls[0]["user"]


def test_make_snippets_fixes_messy_output(transcript, caplog):
    messy = {
        "snippets": [  # wrapped in an object instead of a bare array
            raw("Matrix form", 213.5, 265.0),
            raw("Coefficient matrix", 136.0, 219.0),  # snaps to 218.26, overlapping the next one
            raw("Three pictures", 63.0, 137.0, [q(), q(answer=7)]),  # one bad question
            raw("Too short", 44.0, 57.0),
            raw("", 10, 40),  # no concept
        ]
    }
    with caplog.at_level(logging.WARNING):
        out = sm.make_snippets(transcript, "econ01", ask=fake_ask(messy))

    assert [s["id"] for s in out] == ["econ01-001", "econ01-002", "econ01-003"]
    assert [s["concept"] for s in out] == ["Three pictures", "Coefficient matrix", "Matrix form"]
    assert all(s["lecture_id"] == "econ01" for s in out)
    starts = {seg["start"] for seg in transcript}
    ends = {seg["end"] for seg in transcript}
    for s in out:
        assert s["start"] in starts and s["end"] in ends
        assert list(s) == ["id", "lecture_id", "concept", "start", "end", "summary", "text", "questions"]
    for a, b in zip(out, out[1:]):
        assert a["end"] <= b["start"]
    assert len(out[0]["questions"]) == 1
    assert out[0]["text"].startswith("This is my plan.")
    assert "Okay, so can I do an example?" in out[0]["text"]
    assert "So, take an example." not in out[0]["text"]


def test_text_is_joined_from_segments_inside_range(transcript):
    text = sm.text_between(transcript, 62.68, 69.34)
    assert text == "This is my plan. The fundamental problem of linear algebra,"


def test_long_snippet_is_kept_and_flagged(transcript, caplog, capsys):
    with caplog.at_level(logging.WARNING):
        out = sm.make_snippets(transcript, "demo01", ask=fake_ask([raw("Everything", 62.68, 264.63)]))
    assert len(out) == 1 and out[0]["end"] - out[0]["start"] > sm.MAX_SECONDS
    assert "over 150 s" in caplog.text
    sm.print_table(out)
    assert capsys.readouterr().out.splitlines()[2].endswith("!")


def test_windows_are_merged_without_duplicates(transcript, monkeypatch):
    monkeypatch.setattr(sm, "WINDOW_CHARS", 2500)
    monkeypatch.setattr(sm, "OVERLAP_CHARS", 800)
    topics = [raw("Pictures", 62.68, 137.49), raw("Matrix", 137.49, 212.94), raw("Ax=b", 212.94, 266.74)]

    def per_window(user):
        times = [float(line[1 : line.index("]")]) for line in user.splitlines() if line.startswith("[")]
        lo, hi = min(times), max(times)
        # each window returns every topic it can see, clipped to the window
        return [
            {**t, "start": max(t["start"], lo), "end": min(t["end"], hi + 3)}
            for t in topics
            if t["start"] < hi and t["end"] > lo
        ]

    ask = fake_ask(per_window)
    out = sm.make_snippets(transcript, "demo01", ask=ask)
    assert len(ask.calls) > 1
    assert "part 1 of" in ask.calls[0]["user"]
    concepts = [s["concept"] for s in out]
    assert len(concepts) == len(set(concepts))
    for a, b in zip(out, out[1:]):
        assert a["end"] <= b["start"]


def test_empty_transcript_raises():
    with pytest.raises(ValueError):
        sm.make_snippets([], "x", ask=fake_ask([]))


def test_non_list_output_raises(transcript):
    with pytest.raises(ValueError):
        sm.make_snippets(transcript, "x", ask=fake_ask("just text"))


def test_parse_json_strips_fences():
    assert parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json('Here you go:\n[{"a": 1}]') == [{"a": 1}]


# ---------------------------------------------------------------- CLI


def test_cli_with_transcript_writes_lecture_folder(tmp_path, monkeypatch, capsys, fixture_snippets):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(lectures.load_transcript("demo01")), encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path / "lectures")
    llm_out = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    monkeypatch.setattr(sm, "ask_json", fake_ask(llm_out))

    rc = sm.main(
        ["--transcript", str(src), "--video-url", "https://youtu.be/ZK3O402wf1c", "--lecture-id", "lin01",
         "--title", "Lecture 1", "--lecture-number", "1"]
    )

    assert rc == 0
    folder = tmp_path / "lectures" / "lin01"
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"lecture_id": "lin01", "title": "Lecture 1", "course": "", "lecture_number": 1,
                    "video_url": "https://youtu.be/ZK3O402wf1c"}
    saved = json.loads((folder / "snippets.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in saved] == ["lin01-001", "lin01-002", "lin01-003"]
    assert (folder / "transcript.json").exists()
    table = capsys.readouterr().out
    assert "lin01-002" in table and "02:17-03:33" in table


def test_cli_requires_video_url_with_transcript(tmp_path):
    with pytest.raises(SystemExit):
        sm.main(["--transcript", str(tmp_path / "t.json"), "--lecture-id", "x"])


def _no_api(*a, **k):
    raise AssertionError("API must not be called")


def test_cli_export_prompt_writes_exact_prompt_without_api(tmp_path, monkeypatch, capsys):
    src = tmp_path / "in.json"
    transcript = lectures.load_transcript("demo01")
    src.write_text(json.dumps(transcript), encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path / "lectures")
    monkeypatch.setattr(sm, "ask_json", _no_api)
    out = tmp_path / "prompt.txt"

    rc = sm.main(["--transcript", str(src), "--video-url", "https://youtu.be/x", "--lecture-id", "lin01",
                  "--title", "Lecture 1", "--export-prompt", str(out)])

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert sm.SNIPPET_PROMPT in text
    assert sm.build_user_messages(transcript, "Lecture 1")[0] in text
    folder = tmp_path / "lectures" / "lin01"
    assert (folder / "transcript.json").exists() and (folder / "meta.json").exists()
    assert not (folder / "snippets.json").exists()


def test_export_prompt_writes_one_file_per_window(tmp_path, transcript, monkeypatch):
    monkeypatch.setattr(sm, "WINDOW_CHARS", 2500)
    monkeypatch.setattr(sm, "OVERLAP_CHARS", 800)
    paths = sm.export_prompt(transcript, tmp_path / "p.txt", "T")
    assert len(paths) > 1
    assert [p.name for p in paths[:2]] == ["p.part1.txt", "p.part2.txt"]
    assert "part 1 of" in paths[0].read_text(encoding="utf-8")


def test_cli_import_raw_uses_saved_transcript(tmp_path, monkeypatch, capsys, fixture_snippets):
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path / "lectures")
    monkeypatch.setattr(sm, "ask_json", _no_api)
    lectures.save_transcript("lin01", json.loads(
        (config.ROOT_DIR / "data" / "lectures" / "demo01" / "transcript.json").read_text(encoding="utf-8")))
    raw_items = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    raw_items[0]["start"] += 1.3  # off-boundary time gets snapped
    raw_file = tmp_path / "raw.json"
    raw_file.write_text("```json\n" + json.dumps(raw_items) + "\n```", encoding="utf-8")

    rc = sm.main(["--lecture-id", "lin01", "--import-raw", str(raw_file)])

    assert rc == 0
    saved = lectures.load_snippets("lin01")
    assert [s["id"] for s in saved] == ["lin01-001", "lin01-002", "lin01-003"]
    assert saved[0]["start"] == fixture_snippets[0]["start"]
    assert saved[0]["text"] == fixture_snippets[0]["text"]
    assert "lin01-002" in capsys.readouterr().out


def test_cli_without_source_or_import_errors():
    with pytest.raises(SystemExit):
        sm.main(["--lecture-id", "x"])


def test_warn_answer_skew(caplog):
    skewed = [q(1) for _ in range(9)] + [q(0)]
    with caplog.at_level(logging.WARNING):
        assert sm.warn_answer_skew(skewed)
    assert "option B in 9 of 10" in caplog.text
    assert not sm.warn_answer_skew([q(i % 4) for i in range(12)])
    assert not sm.warn_answer_skew([q(1)] * 3)  # too few to judge


# ---------------------------------------------------------------- round 5: answer balance, purpose, verification


def _snips(n, answer=1):
    return [{"id": f"lec-{i:03d}", "concept": f"c{i}", "start": i * 60.0, "end": i * 60.0 + 60,
             "summary": "A. B.", "text": f"excerpt {i}",
             "questions": [q(answer, options=[f"o{i}{j}{k}" for k in range(4)]) for j in range(2)]}
            for i in range(n)]


def test_balance_answers_spreads_evenly_and_keeps_right_option():
    snippets = _snips(20)
    right = [[qq["options"][qq["answer"]] for qq in s["questions"]] for s in snippets]
    sm.balance_answers(snippets)
    counts = [sum(qq["answer"] == i for s in snippets for qq in s["questions"]) for i in range(4)]
    assert counts == [10, 10, 10, 10]
    assert [[qq["options"][qq["answer"]] for qq in s["questions"]] for s in snippets] == right
    assert sm.balance_answers(copy.deepcopy(snippets)) == sm.balance_answers(snippets)  # deterministic


def test_finalize_balances_answers(transcript, fixture_snippets):
    raw_items = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    for s in raw_items:
        for qq in s["questions"]:
            right = qq["options"].pop(qq["answer"])
            qq["options"].insert(1, right)
            qq["answer"] = 1
    out = sm.finalize(raw_items, transcript, "demo01")
    answers = [qq["answer"] for s in out for qq in s["questions"]]
    assert max(answers.count(i) for i in range(4)) == 2 and len(set(answers)) == 4


def test_call_passes_purpose_only_if_supported():
    seen = []

    def new_ask(system, user, max_tokens=0, purpose="runtime"):
        seen.append(purpose)

    def old_ask(system, user, max_tokens=0):
        seen.append("none")

    sm._call(new_ask, "s", "u", 10, "preprocess")
    sm._call(old_ask, "s", "u", 10, "preprocess")
    sm._call(lambda *a, **kw: seen.append(kw.get("purpose")), "s", "u", 10, "runtime")
    assert seen == ["preprocess", "none", "runtime"]


def test_prompt_has_round5_rules():
    p = sm.SNIPPET_PROMPT
    assert "hard limit" in p and "45-120 seconds" in p
    assert 'never write "The\nprofessor says' in p
    assert "No pure recall questions" in p and "No joke or absurd options" in p
    assert "Spread the correct option" not in p  # handled in code now
    assert sm.QUESTION_RULES.startswith("questions: exactly 2") and "LANGUAGE" not in sm.QUESTION_RULES
    assert sm.QUESTION_RULES in sm.REGEN_PROMPT


def test_quality_warnings():
    s = {"id": "x-001", "concept": "c", "start": 0, "end": 42, "summary": "The professor says hi. Then more."}
    w = sm.quality_warnings([s, {**s, "id": "x-002", "end": 60, "summary": "Opportunity cost is X. Y."}])
    assert len(w) == 2 and "42 s" in w[0] and "framing" in w[1]


class VerifyFake:
    """Fake ask: answers verification and regeneration prompts from scripted tables.

    picks/repicks: {question_id: letter or None}; missing ids answer with the key's letter.
    """

    def __init__(self, picks=None, regen=None, repicks=None, fail_verify=False):
        self.picks, self.regen, self.repicks = picks or {}, regen or {}, repicks or {}
        self.fail_verify = fail_verify
        self.keys = {}
        self.calls = []

    def __call__(self, system, user, max_tokens=None, purpose=None):
        self.calls.append({"system": system, "user": user, "purpose": purpose})
        if system == sm.VERIFY_PROMPT:
            if self.fail_verify:
                raise RuntimeError("down")
            first_excerpt = user.split("### Excerpt ")[1].split("\n")[0]
            table = self.repicks if "_" in first_excerpt else self.picks
            out = []
            for qid, opts in re.findall(r"^Question (\S+): .*\n((?:[A-D]\) .*\n?){4})", user, flags=re.M):
                out.append({"id": qid, "choice": table[qid] if qid in table else self.keys.get(qid)})
            return {"answers": out}
        if system == sm.REGEN_PROMPT:
            ids = re.findall(r"^### Item (\S+)", user, flags=re.M)
            return {"questions": [{"id": i, **self.regen[i]} for i in ids if i in self.regen]}
        raise AssertionError("unexpected prompt")


def _keys(snippets):
    return {f"{s['id']}#{i}": "ABCD"[qq["answer"]] for s in snippets for i, qq in enumerate(s["questions"])}


def test_verify_all_agree():
    snippets = sm.balance_answers(_snips(3))
    fake = VerifyFake()
    fake.keys = _keys(snippets)
    stats = sm.verify_and_repair(snippets, fake)
    assert stats["questions"] == 6 and stats["agreed"] == 6 and stats["regenerated"] == 0
    assert [c["purpose"] for c in fake.calls] == ["runtime"]
    assert "### Excerpt lec-000\nexcerpt 0" in fake.calls[0]["user"]


def test_verify_regenerates_fixes_and_drops():
    snippets = sm.balance_answers(_snips(2))
    keys = _keys(snippets)
    wrong = "ABCD"[(snippets[0]["questions"][0]["answer"] + 1) % 4]
    picks = {"lec-000#0": wrong, "lec-000#1": None, "lec-001#1": None}
    regen = {"lec-000#0": q(2, options=["n0", "n1", "n2", "n3"]),
             "lec-000#1": q(0, options=["m0", "m1", "m2", "m3"])}  # lec-001#1 gets no replacement
    repicks = {"lec-000#0": "C", "lec-000#1": "B"}  # first replacement agrees with its key, second doesn't
    fake = VerifyFake(picks, regen, repicks)
    fake.keys = keys
    stats = sm.verify_and_repair(snippets, fake)
    assert stats["regenerated"] == 3 and stats["fixed"] == 1 and stats["dropped"] == 2 and stats["agreed"] == 1
    assert len(snippets[0]["questions"]) == 1 and len(snippets[1]["questions"]) == 1
    new = snippets[0]["questions"][0]
    assert set(new["options"]) == {"n0", "n1", "n2", "n3"} and new["options"][new["answer"]] == "n2"
    assert [c["purpose"] for c in fake.calls] == ["runtime", "preprocess", "runtime"]
    assert "Key: " in fake.calls[1]["user"] and "could not answer" in fake.calls[1]["user"]
    assert "1 regenerated and fixed, 2 dropped" in sm.format_verification(stats)


def test_verify_warns_when_snippet_loses_all_questions(caplog):
    snippets = sm.balance_answers(_snips(1))
    with caplog.at_level(logging.WARNING):
        stats = sm.verify_and_repair(snippets, VerifyFake({"lec-000#0": None, "lec-000#1": None}))
    assert stats["no_questions"] == ["lec-000"] and snippets[0]["questions"] == []
    assert "no questions left" in caplog.text


def test_verify_call_failure_keeps_questions_unchecked():
    snippets = sm.balance_answers(_snips(2))
    stats = sm.verify_and_repair(snippets, VerifyFake(fail_verify=True))
    assert stats["unchecked"] == 4 and stats["dropped"] == 0 and stats["verified"] is False
    assert all(len(s["questions"]) == 2 for s in snippets)
    summary = sm.format_verification(stats)
    assert "unchecked" in summary and "NOT fully verified" in summary


def test_verify_recheck_failure_drops_replacement_rather_than_reporting_verified():
    snippets = sm.balance_answers(_snips(1))
    fake = VerifyFake(
        picks={"lec-000#0": None},
        regen={"lec-000#0": q(2, options=["n0", "n1", "n2", "n3"])},
        repicks={},  # missing from repicks -> stays unchecked on re-verify
    )
    fake.keys = _keys(snippets)
    # Remove lec-000#0 from fake.keys after initial picks so re-verify returns None/unchecked.
    orig_call = fake.__call__

    def call(system, user, max_tokens=None, purpose=None):
        if system == sm.VERIFY_PROMPT and "_" in user.split("### Excerpt ")[1].split("\n")[0]:
            raise RuntimeError("re-verify failed")
        return orig_call(system, user, max_tokens, purpose)

    stats = sm.verify_and_repair(snippets, call)
    assert stats["fixed"] == 0 and stats["dropped"] == 1
    assert len(snippets[0]["questions"]) == 1


def test_quality_warnings_flags_empty_questions_list():
    s = {"id": "x-001", "concept": "c", "start": 0, "end": 60, "summary": "A. B.", "questions": []}
    warnings = sm.quality_warnings([s])
    assert len(warnings) == 1 and "no questions" in warnings[0]


def test_verify_warns_format_lists_no_questions_ids():
    snippets = sm.balance_answers(_snips(1))
    stats = sm.verify_and_repair(snippets, VerifyFake({"lec-000#0": None, "lec-000#1": None}))
    assert stats["verified"] is True
    assert "Warning: no questions left for lec-000" in sm.format_verification(stats)


def test_verify_batches_by_input_budget(monkeypatch):
    snippets = sm.balance_answers(_snips(6))
    for s in snippets:
        s["text"] = "x" * 3000
    monkeypatch.setattr(sm, "_budget", lambda: 8000)
    fake = VerifyFake()
    fake.keys = _keys(snippets)
    stats = sm.verify_and_repair(snippets, fake)
    assert stats["agreed"] == 12
    assert len(fake.calls) >= 3 and all(len(c["user"]) <= 8000 for c in fake.calls)


def test_make_snippets_uses_preprocess_then_verifies(transcript, fixture_snippets):
    llm_out = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    calls = []

    def ask(system, user, max_tokens=None, purpose=None):
        calls.append((system, purpose))
        if system == sm.SNIPPET_PROMPT:
            return copy.deepcopy(llm_out)
        if system == sm.VERIFY_PROMPT:
            return {"answers": [{"id": i, "choice": "A"} for i in re.findall(r"^Question (\S+):", user, flags=re.M)]}
        return {"questions": []}  # regeneration produces nothing usable -> failures dropped

    stats = {}
    out = sm.make_snippets(transcript, "demo01", ask=ask, stats=stats)
    assert calls[0] == (sm.SNIPPET_PROMPT, "preprocess") and calls[1] == (sm.VERIFY_PROMPT, "runtime")
    assert stats["questions"] == 6 and stats["agreed"] + stats["regenerated"] == 6
    assert sum(len(s["questions"]) for s in out) == stats["agreed"]


def test_cli_prints_verification_summary(tmp_path, monkeypatch, capsys, fixture_snippets):
    src = tmp_path / "in.json"
    src.write_text(json.dumps(lectures.load_transcript("demo01")), encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path / "lectures")
    llm_out = [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in fixture_snippets]
    fake = VerifyFake()

    def ask(system, user, max_tokens=None, purpose=None):
        if system == sm.SNIPPET_PROMPT:
            out = copy.deepcopy(llm_out)
            # keys after balancing aren't known yet; the verify fake answers with them via sm state
            return out
        return fake(system, user, max_tokens, purpose)

    real_verify = sm.verify_and_repair

    def verify_with_keys(snippets, a=None):
        fake.keys = _keys(snippets)
        return real_verify(snippets, a)

    monkeypatch.setattr(sm, "verify_and_repair", verify_with_keys)
    monkeypatch.setattr(sm, "ask_json", ask)
    assert sm.main(["--transcript", str(src), "--video-url", "https://youtu.be/x", "--lecture-id", "lin01"]) == 0
    out = capsys.readouterr().out
    assert "Verification: 6 questions checked: 6 agreed with the key" in out
