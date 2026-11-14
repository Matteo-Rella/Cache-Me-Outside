"""Tests for rewind.store using a temporary database file."""
from __future__ import annotations

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


def test_lecturer_stats(db):
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
    # Snippets with no activity still appear.
    assert by_id["demo01-003"]["times_asked"] == 0

    assert store.lecturer_stats("demo01") == stats
    assert store.lecturer_stats("nope99") == []


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


def test_seed_demo_data(db):
    store.log_attempt("real-student", "demo01-001", 0, 1, True)
    info = store.seed_demo_data()
    assert info["attempts"] == 100
    assert 55 <= info["asks"] <= 70

    stats = store.lecturer_stats("demo01")
    worst = max(stats, key=lambda r: r["fail_rate"])
    assert worst["snippet_id"] == info["hard_snippet"]
    assert worst["fail_rate"] >= 0.4
    others = [r["fail_rate"] for r in stats if r["snippet_id"] != worst["snippet_id"] and r["attempts"]]
    assert all(fr < 0.4 for fr in others)

    gaps = store.open_gaps()
    assert len(gaps) == 5
    assert sum(g["count"] for g in gaps) == 7

    # Idempotent and leaves real rows alone.
    store.seed_demo_data()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 101
    assert conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 7
    assert store.student_weak_spots("real-student") == []
