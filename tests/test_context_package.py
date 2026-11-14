"""Tests for rewind.context_package, using a copy of the demo01 fixture in a temp lectures dir."""
from __future__ import annotations

import copy
import io
import json
import shutil
import subprocess
import sys
import zipfile

import pytest

from rewind import config, lectures, snippet_maker
from rewind import context_package as cp

FIXTURE = config.ROOT_DIR / "data" / "lectures" / "demo01"


@pytest.fixture
def lec(tmp_path, monkeypatch):
    """A lecture 'lin01' with demo01's meta and transcript, in a temp LECTURES_DIR."""
    root = tmp_path / "lectures"
    (root / "lin01").mkdir(parents=True)
    meta = json.loads((FIXTURE / "meta.json").read_text(encoding="utf-8"))
    meta["lecture_id"] = "lin01"
    (root / "lin01" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    shutil.copy(FIXTURE / "transcript.json", root / "lin01" / "transcript.json")
    monkeypatch.setattr(config, "LECTURES_DIR", root)
    return "lin01"


@pytest.fixture
def raw_items():
    """The demo01 fixture snippets as a model would return them."""
    snippets = json.loads((FIXTURE / "snippets.json").read_text(encoding="utf-8"))
    return [{k: s[k] for k in ("concept", "start", "end", "summary", "questions")} for s in snippets]


def fenced(items, before="Here is the JSON:", after="Let me know if you need changes."):
    return f"{before}\n\n```json\n{json.dumps(items, indent=2)}\n```\n\n{after}\n"


def unzip(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n).decode("utf-8") for n in z.namelist()}


# ---------------------------------------------------------------- package


def test_package_contains_all_files(lec):
    files = unzip(cp.build_package(lec))
    assert {"START-HERE.md", "PROMPT-part-1.md", "AGENT.md", "transcript.json", "schema.json",
            "meta.json", "check.py"} <= set(files)
    assert json.loads(files["meta.json"])["lecture_id"] == lec
    assert json.loads(files["transcript.json"]) == lectures.load_transcript(lec)
    assert json.loads(files["schema.json"])["type"] == "array"
    assert "AGENT.md" in files["START-HERE.md"]
    assert f"python -m rewind.context_package import {lec}" in files["AGENT.md"]


def test_prompt_part_is_paste_ready(lec):
    [part] = cp.build_chat_prompt(lec)
    assert "Each snippet lasts 45-120 seconds" in part  # full SNIPPET_PROMPT instructions
    assert "```json" in part and "single ```json code block" in part
    assert "no code fences" not in part  # the API-only rule is replaced for chats
    assert "Le Chatelier" in part  # one example
    assert "[62.7] " in part or "[62.68] " in part or "[62.6" in part  # transcript lines
    assert len(part) < cp.PART_MAX_CHARS


def test_example_matches_schema_rules():
    assert cp.check_answer(copy.deepcopy(cp.EXAMPLE), 0, 10_000) == []


def test_long_lecture_splits_into_parts_with_lookahead(lec, monkeypatch):
    monkeypatch.setattr(cp, "PART_MAX_SECONDS", 60)
    parts = cp.build_chat_prompt(lec)
    ranges = cp.part_ranges(lec)
    assert len(parts) == len(ranges) >= 3
    assert "part 1 of %d" % len(parts) in parts[0]
    assert "Continue from part 1" in parts[1] and "output only snippets for this slice" in parts[1]
    assert "LOOKAHEAD" in parts[0] and "LOOKAHEAD" not in parts[-1]
    # Every transcript line appears in exactly one part (ignoring lookahead lines).
    body = [p.split("## Transcript")[1].split("----- LOOKAHEAD")[0] for p in parts]
    starts = [f"[{s['start']:.1f}] " for s in lectures.load_transcript(lec)]
    assert all(sum(b.count(s) for b in body) == 1 for s in starts)
    assert all(nxt[0] >= prev[1] for prev, nxt in zip(ranges, ranges[1:]))  # in order, no overlap
    assert cp.build_chat_prompt(lec, part=2) == [parts[1]]
    with pytest.raises(ValueError):
        cp.build_chat_prompt(lec, part=len(parts) + 1)


def test_parts_respect_char_limit():
    segs = [{"start": i * 2.0, "end": i * 2.0 + 2, "text": "word " * 30} for i in range(600)]
    parts = cp.split_even(segs, max_chars=20_000, max_seconds=10_000)
    assert len(parts) > 1
    assert all(len(snippet_maker.format_transcript(p)) <= 20_000 for p in parts)
    assert sum(len(p) for p in parts) == len(segs)


def test_split_prefers_sentence_end():
    segs = [{"start": float(i * 10), "end": float(i * 10 + 10), "text": "word."} for i in range(20)]
    for i in range(20):
        if i != 8:
            segs[i]["text"] = "and so on"
    [_, second] = cp.split_even(segs, max_chars=10**6, max_seconds=100)
    assert second[0]["start"] == 90.0  # line after the only finished sentence near the middle


# ---------------------------------------------------------------- extracting JSON from replies


def test_extract_from_chat_reply_with_prose(raw_items):
    items, errors = cp.extract_snippets(fenced(raw_items))
    assert errors == [] and items == raw_items


def test_extract_bare_array_and_wrapped_object(raw_items):
    assert cp.extract_snippets(json.dumps(raw_items))[0] == raw_items
    assert cp.extract_snippets(json.dumps({"snippets": raw_items}))[0] == raw_items


def test_extract_merges_several_replies(raw_items):
    text = fenced(raw_items[:1], after="Ready for part 2.") + fenced(raw_items[1:], before="Part 2:")
    assert cp.extract_snippets(text)[0] == raw_items


def test_extract_reports_cut_off_json(raw_items):
    text = "```json\n" + json.dumps(raw_items)[:-40]  # reply stopped mid-way, no closing fence
    items, errors = cp.extract_snippets(text)
    assert errors and "cut off" in errors[0]


def test_extract_without_json():
    items, errors = cp.extract_snippets("Sorry, I can't watch videos.")
    assert items == [] and "couldn't find" in errors[0]


def test_extract_ignores_transcript_arrays(lec):
    items, errors = cp.extract_snippets(json.dumps(lectures.load_transcript(lec)))
    assert items == [] and errors


# ---------------------------------------------------------------- importing


def test_import_ok_saves_snippets_and_raw(lec, raw_items):
    raw_items[0]["start"] += 1.2  # off-boundary time gets snapped by post-processing
    result = cp.import_answer(lec, fenced(raw_items))
    assert result == {"ok": True, "snippets_count": 3, "errors": [], "fix_message": ""}
    saved = lectures.load_snippets(lec)
    assert [s["id"] for s in saved] == ["lin01-001", "lin01-002", "lin01-003"]
    assert saved[0]["start"] == 62.68 and saved[0]["text"]
    assert json.loads((lectures.lecture_dir(lec) / "raw_llm_output.json").read_text(encoding="utf-8"))


def test_import_dedupes_repeated_part_and_ignores_prompt_example(lec, raw_items):
    pasted_prompt = cp.build_chat_prompt(lec)[0]  # whole chat pasted, including the example
    text = pasted_prompt + fenced(raw_items) + fenced(raw_items)
    result = cp.import_answer(lec, text)
    assert result["ok"] and result["snippets_count"] == 3


def test_import_broken_reply_returns_fix_message_and_saves_nothing(lec, raw_items):
    bad = copy.deepcopy(raw_items)
    bad[0]["questions"][0]["options"] = ["A", "B", "C"]
    bad[0]["questions"][1]["answer"] = 4
    bad[1]["end"] = bad[1]["start"] + 400
    del bad[2]["summary"]
    result = cp.import_answer(lec, fenced(bad))
    assert result["ok"] is False and result["snippets_count"] == 0
    errs = "\n".join(result["errors"])
    assert 'Snippet 1 ("Three pictures of a linear system"), question 1 must have exactly 4' in errs
    assert "answer must be the 0-based index" in errs and "got 4" in errs
    assert "seconds long" in errs and "Split it" in errs
    assert "Snippet 3" in errs and "missing: summary" in errs
    fix = result["fix_message"]
    assert fix.startswith("Your answer had these problems:")
    assert all(e in fix for e in result["errors"])
    assert "```json" in fix and "complete corrected JSON" in fix
    assert lectures.load_snippets(lec) == []


def test_import_overlap_and_short_snippet(lec, raw_items):
    bad = copy.deepcopy(raw_items)
    bad[1]["start"] = bad[0]["start"] + 30  # overlaps snippet 1
    bad[2]["end"] = bad[2]["start"] + 10
    errors = cp.import_answer(lec, json.dumps(bad))["errors"]
    assert any("overlaps" in e for e in errors)
    assert any("only 10 seconds" in e for e in errors)


def test_import_flags_answer_skew():
    item = copy.deepcopy(cp.EXAMPLE[0])
    items = []
    for i in range(5):
        it = copy.deepcopy(item)
        it["start"], it["end"] = 100.0 * i, 100.0 * i + 60
        for q in it["questions"]:
            q["answer"] = 1
        items.append(it)
    errors = cp.check_answer(items, 0, 1000)
    assert any("option 1 in 10 of 10" in e for e in errors)


def test_missing_part_is_reported():
    parts = [(0.0, 900.0), (900.0, 1800.0)]  # 15-minute parts
    a = copy.deepcopy(cp.EXAMPLE[0]); a["start"], a["end"] = 100.0, 180.0
    errors = cp.check_answer([a], 0, 1800, parts)
    assert any("No snippets for part 2 (15:00-30:00) were pasted" in e for e in errors)
    # A short part may be all admin talk: no error.
    assert cp.check_answer([a], 0, 1000, [(0.0, 900.0), (900.0, 1000.0)]) == []


def test_lookahead_overlap_across_parts_is_allowed():
    parts = [(0.0, 300.0), (300.0, 600.0)]
    a = copy.deepcopy(cp.EXAMPLE[0]); a["start"], a["end"] = 200.0, 340.0  # runs into part 2's lookahead
    b = copy.deepcopy(cp.EXAMPLE[0]); b["start"], b["end"] = 300.0, 400.0
    b["concept"] = "other"
    assert not any("overlap" in e for e in cp.check_answer([a, b], 0, 600, parts))


def test_import_empty_and_garbage(lec):
    assert cp.import_answer(lec, "   ")["errors"][0].startswith("The answer is empty")
    r = cp.import_answer(lec, "I can't help with that.")
    assert not r["ok"] and r["fix_message"]


def test_too_many_errors_are_capped():
    items = [{"concept": "x%d" % i} for i in range(60)]
    errors = cp.check_answer(items, 0, 100)
    assert len(errors) == 26 and errors[-1].startswith("... and 35 more")


# ---------------------------------------------------------------- check.py and CLI


def test_check_py_runs_standalone(lec, raw_items, tmp_path):
    folder = tmp_path / "pkg"
    with zipfile.ZipFile(io.BytesIO(cp.build_package(lec))) as z:
        z.extractall(folder)
    good, bad = folder / "good.json", folder / "bad.json"
    good.write_text(json.dumps(raw_items), encoding="utf-8")
    broken = copy.deepcopy(raw_items)
    broken[0]["questions"] = broken[0]["questions"][:1]
    bad.write_text(fenced(broken), encoding="utf-8")
    run = lambda p: subprocess.run([sys.executable, "-I", "check.py", str(p)], cwd=folder,
                                   capture_output=True, text=True)
    ok = run(good)
    assert ok.returncode == 0 and "OK: 3 snippets" in ok.stdout
    fail = run(bad)
    assert fail.returncode == 1 and "exactly 2 questions" in fail.stdout


def test_cli_import(lec, raw_items, tmp_path, capsys):
    good, bad = tmp_path / "good.txt", tmp_path / "bad.txt"
    good.write_text(fenced(raw_items), encoding="utf-8")
    bad.write_text("no json here", encoding="utf-8")
    assert cp.main(["import", lec, str(bad)]) == 1
    assert "Your answer had these problems" in capsys.readouterr().out
    assert cp.main(["import", lec, str(good)]) == 0
    assert "imported 3 snippets" in capsys.readouterr().out


def test_cli_build_and_prompt(lec, tmp_path, capsys):
    out = tmp_path / "p.zip"
    assert cp.main(["build", lec, str(out)]) == 0
    assert zipfile.is_zipfile(out)
    assert cp.main(["prompt", lec, "1"]) == 0
    assert "# Rewind snippet request: part 1 of 1" in capsys.readouterr().out


# ---------------------------------------------------------------- newest answer wins


def test_correction_replaces_earlier_answer(raw_items):
    bad = copy.deepcopy(raw_items)
    bad[0]["questions"][0]["answer"] = 9
    text = fenced(bad, after="") + "Your answer had these problems: ...\n" + fenced(raw_items, before="Fixed:")
    items, errors = cp.extract_snippets(text)
    assert errors == [] and items == raw_items


def test_broken_block_superseded_by_later_valid_block(raw_items):
    text = "```json\n" + json.dumps(raw_items)[:-30] + "\n```\nSorry, here it is again:\n" + fenced(raw_items)
    items, errors = cp.extract_snippets(text)
    assert errors == [] and items == raw_items
    items, errors = cp.extract_snippets(fenced(raw_items) + "```json\n" + json.dumps(raw_items)[:-30])
    assert errors and "cut off" in errors[0]


def test_continuation_block_is_appended(raw_items):
    text = fenced(raw_items[:2], after="(continued)") + fenced(raw_items[2:], before="")
    assert cp.extract_snippets(text)[0] == raw_items


def test_whole_chat_paste_ignores_examples_in_prompts():
    early = copy.deepcopy(cp.EXAMPLE[0]); early["concept"] = "Real snippet"; early["start"], early["end"] = 450.0, 520.0
    late = copy.deepcopy(early); late["concept"] = "Later"; late["start"], late["end"] = 1200.0, 1290.0
    prompt = "# part\n```json\n" + json.dumps(cp.EXAMPLE) + "\n```\n"
    text = prompt + fenced([early]) + prompt + fenced([late])
    items, errors = cp.extract_snippets(text, ignore=cp.EXAMPLE)
    assert [i["concept"] for i in items] == ["Real snippet", "Later"]


def test_fix_message_for_multi_part_asks_per_part():
    msg = cp.fix_message_for(["x"], parts_count=3)
    assert "each part that had problems" in msg
    assert "all snippets" in cp.fix_message_for(["x"], parts_count=1)


def test_check_answer_rejects_empty_question_text_explanation_and_bool_times(raw_items):
    bad = copy.deepcopy(raw_items)
    bad[0]["questions"][0]["q"] = "   "
    bad[0]["questions"][1]["explanation"] = ""
    bad[1]["start"] = True
    errors = cp.check_answer(bad, 0, 1000)
    assert any('"q" must be a non-empty string' in e for e in errors)
    assert any('"explanation" must be a non-empty string' in e for e in errors)
    assert any("start and end must be numbers" in e for e in errors)


def test_import_empty_transcript_returns_failure_dict(tmp_path, monkeypatch):
    root = tmp_path / "lectures"
    (root / "empty01").mkdir(parents=True)
    (root / "empty01" / "meta.json").write_text(json.dumps({"lecture_id": "empty01", "title": "Empty"}), encoding="utf-8")
    (root / "empty01" / "transcript.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", root)
    res = cp.import_answer("empty01", "[]")
    assert res["ok"] is False and "empty" in res["errors"][0].lower()
