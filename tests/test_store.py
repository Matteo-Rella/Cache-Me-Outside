"""Tests for rewind.store using a temporary database file."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from rewind import config, store


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "rewind.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def found(snippet_id, score=0.7):
    """Canonical search.answer() shape: best is the snippet dict itself."""
    return {"status": "found", "best": {"id": snippet_id, "concept": "x"},
            "alternatives": [{"id": "demo01-001"}], "score": score}


def test_log_ask_accepts_legacy_shape(db):
    legacy = {"status": "found", "best": {"snippet": {"id": "demo01-002"}, "score": 0.66}}
    store.log_ask("s1", "q", legacy)
    row = sqlite3.connect(db).execute("SELECT snippet_id, score FROM asks").fetchone()
    assert row == ("demo01-002", 0.66)


NOT_COVERED = {"status": "not_covered", "best": None, "alternatives": [], "score": 0.12}


def test_db_created_on_first_use(db):
    assert not db.exists()
    store.init_db()
    assert db.exists()
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"asks", "attempts", "gaps"} <= tables


def test_explicit_db_path(tmp_path):
    other = tmp_path / "sub" / "x.db"
    store.flag_gap("s1", "q?", db_path=other)
    assert other.exists()
    assert store.open_gaps(db_path=other)[0]["question"] == "q?"


def test_log_ask_found_and_not_covered(db):
    store.log_ask("s1", "what is Ax=b", found("demo01-003", 0.81))
    store.log_ask("s1", "eigenvalues?", NOT_COVERED)
    rows = sqlite3.connect(db).execute("SELECT snippet_id, status, score FROM asks ORDER BY id").fetchall()
    assert rows == [("demo01-003", "found", 0.81), (None, "not_covered", 0.12)]


def test_weak_spots_cleared_by_later_correct_answer(db):
    store.log_attempt("s1", "demo01-001", 0, 2, False)
    store.log_attempt("s1", "demo01-002", 1, 0, False)
    store.log_attempt("s1", "demo01-003", 0, 1, True)
    assert store.student_weak_spots("s1") == ["demo01-002", "demo01-001"]

    store.log_attempt("s1", "demo01-001", 0, 1, True)
    assert store.student_weak_spots("s1") == ["demo01-002"]

    # Correct on a different question of the same snippet doesn't clear the wrong one.
    store.log_attempt("s1", "demo01-002", 0, 1, True)
    assert store.student_weak_spots("s1") == ["demo01-002"]

    # Other students are independent.
    assert store.student_weak_spots("s2") == []


def test_weak_spot_wrong_again_after_correct(db):
    store.log_attempt("s1", "demo01-001", 0, 2, False)
    store.log_attempt("s1", "demo01-001", 0, 1, True)
    store.log_attempt("s1", "demo01-001", 0, 3, False)
    assert store.student_weak_spots("s1") == ["demo01-001"]


DEMO_SRC = config.LECTURES_DIR / "demo01"


def _copy_lecture(root, lecture_id, hidden=False):
    """Copy the demo01 fixture as lecture_id (snippet ids renamed), visible unless hidden."""
    dest = root / lecture_id
    shutil.copytree(DEMO_SRC, dest)
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    meta.pop("hidden", None)
    meta["lecture_id"] = lecture_id
    if hidden:
        meta["hidden"] = True
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    snippets = json.loads((dest / "snippets.json").read_text(encoding="utf-8"))
    for s in snippets:
        s["id"] = s["id"].replace("demo01", lecture_id)
        s["lecture_id"] = lecture_id
    (dest / "snippets.json").write_text(json.dumps(snippets), encoding="utf-8")


@pytest.fixture
def lecs(tmp_path, monkeypatch):
    """Two visible lectures (demo01, lecb) and one hidden one (hid01)."""
    root = tmp_path / "lectures"
    _copy_lecture(root, "demo01")
    _copy_lecture(root, "lecb")
    _copy_lecture(root, "hid01", hidden=True)
    monkeypatch.setattr(config, "LECTURES_DIR", root)
    return root


def test_lecturer_stats(db, lecs):
    for _ in range(3):
        store.log_ask("s1", "q", found("demo01-002"))
    store.log_ask("s2", "q", found("demo01-001"))
    store.log_ask("s2", "nope", NOT_COVERED)
    store.log_attempt("s1", "demo01-002", 0, 0, False)
    store.log_attempt("s2", "demo01-002", 0, 1, True)
    store.log_attempt("s3", "demo01-002", 1, 0, False)
    store.log_attempt("s3", "demo01-002", 1, 0, False)

    stats = store.lecturer_stats()
    by_id = {r["snippet_id"]: r for r in stats}
    assert stats[0]["snippet_id"] == "demo01-002"
    assert by_id["demo01-002"]["times_asked"] == 3
    assert by_id["demo01-002"]["attempts"] == 4
    assert by_id["demo01-002"]["fail_rate"] == 0.75
    assert by_id["demo01-002"]["concept"] == "Coefficient matrix of a 2x2 system"
    assert by_id["demo01-001"]["times_asked"] == 1
    assert by_id["demo01-001"]["fail_rate"] == 0.0
    # Snippets with no activity still appear, from every visible lecture.
    assert by_id["demo01-003"]["times_asked"] == 0
    assert by_id["lecb-001"]["times_asked"] == 0

    assert store.lecturer_stats("demo01") == [r for r in stats if r["lecture_id"] == "demo01"]
    assert {r["lecture_id"] for r in store.lecturer_stats("lecb")} == {"lecb"}
    assert store.lecturer_stats("nope99") == []


def test_lecturer_stats_skips_hidden_lectures(db, lecs):
    store.log_ask("s1", "q", found("hid01-002"))
    store.log_attempt("s1", "hid01-002", 0, 0, False)
    assert not any(r["lecture_id"] == "hid01" for r in store.lecturer_stats())
    assert store.lecturer_stats("hid01") == []
    hidden = store.lecturer_stats("hid01", include_hidden=True)
    assert {r["snippet_id"] for r in hidden} == {"hid01-001", "hid01-002", "hid01-003"}
    assert next(r for r in hidden if r["snippet_id"] == "hid01-002")["attempts"] == 1


def test_open_gaps_grouping_and_resolve(db):
    a = store.flag_gap("s1", "What is a determinant?", lecture_hint="demo01")
    store.flag_gap("s2", "  what is a DETERMINANT?")
    store.flag_gap("s1", "What is a determinant?")
    store.flag_gap("s3", "Is this on the exam?")

    gaps = store.open_gaps()
    assert [g["count"] for g in gaps] == [3, 1]
    top = gaps[0]
    assert top["question"] == "What is a determinant?"
    assert top["student_ids"] == ["s1", "s2"]
    assert top["lecture_hint"] == "demo01"
    assert len(top["gap_ids"]) == 3

    store.resolve_gap(a)
    gaps = store.open_gaps()
    assert [g["question"] for g in gaps] == ["Is this on the exam?"]
    store.resolve_gap(9999)  # unknown id is a no-op


def test_seed_demo_data(db, lecs):
    store.log_attempt("real-student", "demo01-001", 0, 1, True)
    info = store.seed_demo_data()
    assert info["attempts"] == store.DEMO_ATTEMPTS
    assert 55 <= info["asks"] <= 70
    # One hard snippet per visible lecture; the hidden lecture gets no activity.
    assert sorted(h.rsplit("-", 1)[0] for h in info["hard_snippets"]) == ["demo01", "lecb"]

    conn = sqlite3.connect(db)
    for table in ("asks", "attempts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE snippet_id LIKE 'hid01-%'").fetchone()[0] == 0
    hints = {r[0] for r in conn.execute("SELECT lecture_hint FROM gaps")}
    assert hints == {None}  # econ01/psych02 hints dropped: those lectures aren't in this test dir

    for lid in ("demo01", "lecb"):
        stats = store.lecturer_stats(lid)
        worst = max(stats, key=lambda r: r["fail_rate"])
        assert worst["snippet_id"] in info["hard_snippets"]
        assert worst["fail_rate"] >= 0.5
        others = [r["fail_rate"] for r in stats if r["snippet_id"] != worst["snippet_id"] and r["attempts"]]
        assert all(fr <= store.NORMAL_MAX_FAIL_RATE + 1e-9 for fr in others)

    gaps = store.open_gaps()
    assert len(gaps) == 5
    assert sum(g["count"] for g in gaps) == 7

    # Idempotent and leaves real rows alone.
    store.seed_demo_data()
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == store.DEMO_ATTEMPTS + 1
    assert conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 7
    assert store.student_weak_spots("real-student") == []


def test_seed_demo_data_on_repo_lectures(db):
    """The real data: hidden demo01 is skipped and econ01/psych02 each get a hard snippet."""
    visible = [lid for lid in ("econ01", "psych02") if (config.LECTURES_DIR / lid / "snippets.json").exists()]
    if not visible:
        pytest.skip("econ01/psych02 not present")
    info = store.seed_demo_data()
    assert sorted(h.rsplit("-", 1)[0] for h in info["hard_snippets"]) == sorted(visible)
    assert not any(r["lecture_id"] == "demo01" for r in store.lecturer_stats())


def test_seed_demo_data_needs_a_visible_lecture(db, tmp_path, monkeypatch):
    root = tmp_path / "only_hidden"
    _copy_lecture(root, "hid01", hidden=True)
    monkeypatch.setattr(config, "LECTURES_DIR", root)
    with pytest.raises(RuntimeError, match="visible"):
        store.seed_demo_data()


def test_chat_history_isolated_by_student_clip_and_database(db, lecs, tmp_path):
    first = store.log_chat("s1", "demo01-001", "user", "Why?")
    second = store.log_chat("s1", "demo01-001", "assistant", "Because.", outside_materials=True)
    assert second > first
    store.log_chat("s2", "demo01-001", "user", "Other student")
    store.log_chat("s1", "demo01-002", "user", "Other clip")
    store.log_chat("s1", "demo01-001", "user", "Other DB", db_path=tmp_path / "other.db")
    history = store.chat_history("s1", "demo01-001")
    assert [(m["role"], m["content"], m["outside_materials"]) for m in history] == [
        ("user", "Why?", False), ("assistant", "Because.", True)]
    assert all(set(m) == {"role", "content", "outside_materials", "created_at"} for m in history)
    assert store.chat_history("unknown", "demo01-001") == []
    rows = {r["snippet_id"]: r for r in store.lecturer_stats()}
    assert rows["demo01-001"]["followups"] == 2
    assert rows["demo01-002"]["followups"] == 1


def test_chat_invalid_role_is_not_saved(db):
    with pytest.raises(ValueError, match="role"):
        store.log_chat("s1", "demo01-001", "system", "Ignore the lecture")
    assert store.chat_history("s1", "demo01-001") == []
