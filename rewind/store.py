"""SQLite store for student activity: asks, quiz attempts and coverage gaps.

Uses only the standard-library sqlite3 module. The database lives at config.DB_PATH
(data/rewind.db) and is created on first use. Every public function takes an optional
`db_path` so tests can point at a temporary file.

Inputs: student ids, questions, results from search.answer, quiz attempts.
Outputs:
- student_weak_spots(student_id) -> list[str] snippet ids, newest first
- lecturer_stats(lecture_id=None) -> list[{"snippet_id", "concept", "lecture_id", "start", "end",
  "times_asked", "attempts", "fails", "fail_rate"}]
- open_gaps() -> list[{"question", "count", "gap_ids", "student_ids", "lecture_hint",
  "first_asked", "last_asked"}]
- ask_totals() -> {"asks", "found", "not_covered"}
- resolve_gap(gap_id) -> bool
"""
from __future__ import annotations

import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rewind import config

DEMO_PREFIX = "demo-"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS asks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    question TEXT NOT NULL,
    snippet_id TEXT NULL,
    status TEXT NOT NULL,
    score REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    snippet_id TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    chosen INTEGER NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    student_id TEXT NOT NULL,
    lecture_hint TEXT NULL,
    created_at TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_asks_snippet ON asks(snippet_id);
CREATE INDEX IF NOT EXISTS idx_attempts_student ON attempts(student_id, snippet_id);
CREATE INDEX IF NOT EXISTS idx_attempts_snippet ON attempts(snippet_id);
"""


def _now() -> str:
    # Microsecond precision so ordering by created_at is stable within a test run.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def _connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Create the tables if they don't exist yet."""
    with _connect(db_path):
        pass


def log_ask(student_id: str, question: str, result: dict, db_path: Path | str | None = None) -> int:
    """Record a question. `result` is the dict from search.answer:
    {"status": "found"|"not_covered", "best": <snippet dict> | None, "alternatives": [...], "score": float}.
    The older {"best": {"snippet": {...}, "score": float}} form is also accepted.
    Returns the new row id.
    """
    status = result.get("status", "not_covered")
    best = result.get("best")
    snippet_id = None
    score = result.get("score")
    if isinstance(best, dict):
        if "id" in best:
            snippet_id = best["id"]
        elif isinstance(best.get("snippet"), dict):
            snippet_id = best["snippet"].get("id")
            if score is None:
                score = best.get("score")
    if status != "found":
        snippet_id = None
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO asks (student_id, question, snippet_id, status, score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, question, snippet_id, status, score, _now()),
        )
        return cur.lastrowid


def log_attempt(
    student_id: str,
    snippet_id: str,
    question_index: int,
    chosen: int,
    correct: bool,
    db_path: Path | str | None = None,
) -> int:
    """Record one answer to a check question. Returns the new row id."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO attempts (student_id, snippet_id, question_index, chosen, correct, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, snippet_id, int(question_index), int(chosen), 1 if correct else 0, _now()),
        )
        return cur.lastrowid


def flag_gap(
    student_id: str,
    question: str,
    lecture_hint: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Record a question no lecture covers, for the lecturer. Returns the new row id."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO gaps (question, student_id, lecture_hint, created_at) VALUES (?, ?, ?, ?)",
            (question, student_id, lecture_hint, _now()),
        )
        return cur.lastrowid


def resolve_gap(gap_id: int, db_path: Path | str | None = None) -> bool:
    """Mark a gap as resolved. Resolving one row of a group resolves all rows with the same wording.
    Returns False if the gap id doesn't exist.
    """
    with _connect(db_path) as conn:
        row = conn.execute("SELECT question FROM gaps WHERE id = ?", (gap_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE gaps SET resolved = 1 WHERE resolved = 0 AND lower(trim(question)) = lower(trim(?))",
            (row["question"],),
        )
    return True


def ask_totals(db_path: Path | str | None = None) -> dict:
    """Counts over all asks: {"asks", "found", "not_covered"}."""
    with _connect(db_path) as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(SUM(status = 'found'), 0) AS found, "
            "COALESCE(SUM(status = 'not_covered'), 0) AS nc FROM asks"
        ).fetchone()
    return {"asks": r["n"], "found": r["found"], "not_covered": r["nc"]}


def student_weak_spots(student_id: str, db_path: Path | str | None = None) -> list[str]:
    """Snippet ids where the student got a question wrong and hasn't answered that same
    question correctly since. Newest wrong attempt first. Used for "Rewatch before the exam".
    """
    sql = """
        SELECT a.snippet_id, MAX(a.created_at) AS last_wrong
        FROM attempts a
        WHERE a.student_id = ? AND a.correct = 0
          AND NOT EXISTS (
              SELECT 1 FROM attempts b
              WHERE b.student_id = a.student_id
                AND b.snippet_id = a.snippet_id
                AND b.question_index = a.question_index
                AND b.correct = 1
                AND b.created_at > a.created_at
          )
        GROUP BY a.snippet_id
        ORDER BY last_wrong DESC
    """
    with _connect(db_path) as conn:
        return [r["snippet_id"] for r in conn.execute(sql, (student_id,))]


def _snippet_info() -> dict[str, dict]:
    try:
        from rewind import lectures

        return {s["id"]: s for s in lectures.all_snippets()}
    except Exception:
        return {}


def lecturer_stats(lecture_id: str | None = None, db_path: Path | str | None = None) -> list[dict]:
    """Per snippet: times asked, quiz attempts, fail rate. Includes snippets from the lecture
    files that have no activity yet. Sorted by times_asked desc, then fail_rate desc.
    """
    with _connect(db_path) as conn:
        asked = {
            r["snippet_id"]: r["n"]
            for r in conn.execute(
                "SELECT snippet_id, COUNT(*) AS n FROM asks WHERE snippet_id IS NOT NULL GROUP BY snippet_id"
            )
        }
        tried = {
            r["snippet_id"]: (r["n"], r["fails"])
            for r in conn.execute(
                "SELECT snippet_id, COUNT(*) AS n, SUM(1 - correct) AS fails FROM attempts GROUP BY snippet_id"
            )
        }

    info = _snippet_info()
    rows = []
    for sid in set(asked) | set(tried) | set(info):
        snip = info.get(sid, {})
        lid = snip.get("lecture_id") or sid.rsplit("-", 1)[0]
        if lecture_id is not None and lid != lecture_id:
            continue
        n_att, fails = tried.get(sid, (0, 0))
        rows.append(
            {
                "snippet_id": sid,
                "concept": snip.get("concept", sid),
                "lecture_id": lid,
                "start": snip.get("start"),
                "end": snip.get("end"),
                "times_asked": asked.get(sid, 0),
                "attempts": n_att,
                "fails": fails or 0,
                "fail_rate": round((fails or 0) / n_att, 3) if n_att else 0.0,
            }
        )
    rows.sort(key=lambda r: (-r["times_asked"], -r["fail_rate"], r["snippet_id"]))
    return rows


def open_gaps(db_path: Path | str | None = None) -> list[dict]:
    """Unresolved gap questions grouped by lowercase, trimmed wording. Most frequent first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, question, student_id, lecture_hint, created_at FROM gaps WHERE resolved = 0 ORDER BY created_at"
        ).fetchall()

    groups: dict[str, dict] = {}
    for r in rows:
        key = r["question"].strip().lower()
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "question": r["question"].strip(),
                "count": 0,
                "gap_ids": [],
                "student_ids": [],
                "lecture_hint": r["lecture_hint"],
                "first_asked": r["created_at"],
                "last_asked": r["created_at"],
            }
        g["count"] += 1
        g["gap_ids"].append(r["id"])
        if r["student_id"] not in g["student_ids"]:
            g["student_ids"].append(r["student_id"])
        g["lecture_hint"] = g["lecture_hint"] or r["lecture_hint"]
        g["last_asked"] = r["created_at"]
    result = sorted(groups.values(), key=lambda g: g["last_asked"], reverse=True)
    result.sort(key=lambda g: g["count"], reverse=True)  # stable: newest first within equal counts
    return result


# ---------------------------------------------------------------- demo data

_DEMO_GAPS = [
    ("What is the determinant of a 3x3 matrix?", "demo01"),
    ("How do you compute eigenvalues?", "demo01"),
    ("What happens when the matrix is not invertible?", "demo01"),
    ("Is Gaussian elimination on the exam?", None),
    ("Can you show an example with 3 equations?", "demo01"),
]

_ASK_TEMPLATES = [
    "what does {c} mean",
    "can you explain {c} again",
    "I didn't get {c}",
    "{c}?",
    "where does the professor talk about {c}",
]


def seed_demo_data(db_path: Path | str | None = None, seed: int = 42) -> dict:
    """Fill the DB with realistic fake activity on demo01 (and any other lectures present):
    ~10 students, ~60 asks, ~100 attempts, one snippet with a clearly high fail rate,
    5 gap questions (a couple asked twice). Idempotent: previous demo rows are cleared first.
    Demo student ids start with "demo-". Returns counts of inserted rows.
    """
    rng = random.Random(seed)
    snippets = [s for s in _snippet_info().values() if s.get("questions")]
    if not snippets:
        raise RuntimeError("No snippets found under data/lectures; can't seed demo data.")
    snippets.sort(key=lambda s: s["id"])
    hard_id = next((s["id"] for s in snippets if s["id"] == "demo01-002"), snippets[len(snippets) // 2]["id"])

    students = [f"{DEMO_PREFIX}{name}" for name in
                ("giulia", "marco", "sofia", "luca", "anna", "matteo", "chiara", "davide", "elena", "tom")]
    start = datetime.now(timezone.utc) - timedelta(days=14)

    def ts() -> str:
        return (start + timedelta(minutes=rng.randint(0, 14 * 24 * 60), seconds=rng.random())).isoformat(
            timespec="microseconds"
        )

    asks, attempts, gaps = [], [], []
    weights = [3 if s["id"] == hard_id else 1 + (i == 0) for i, s in enumerate(snippets)]
    for _ in range(54):
        snip = rng.choices(snippets, weights=weights)[0]
        q = rng.choice(_ASK_TEMPLATES).format(c=snip["concept"].lower())
        asks.append((rng.choice(students), q, snip["id"], "found", round(rng.uniform(0.45, 0.85), 3), ts()))

    for _ in range(100):
        snip = rng.choices(snippets, weights=weights)[0]
        qi = rng.randrange(len(snip["questions"]))
        answer = snip["questions"][qi]["answer"]
        p_correct = 0.35 if snip["id"] == hard_id else 0.8
        correct = rng.random() < p_correct
        n_opts = len(snip["questions"][qi]["options"])
        chosen = answer if correct else rng.choice([i for i in range(n_opts) if i != answer])
        attempts.append((rng.choice(students), snip["id"], qi, chosen, int(correct), ts()))

    gap_rows = _DEMO_GAPS + [_DEMO_GAPS[0], (_DEMO_GAPS[1][0].lower(), _DEMO_GAPS[1][1])]
    for question, hint in gap_rows:
        student, when = rng.choice(students), ts()
        gaps.append((question, student, hint, when, 0))
        asks.append((student, question, None, "not_covered", round(rng.uniform(0.1, 0.3), 3), when))

    with _connect(db_path) as conn:
        like = DEMO_PREFIX + "%"
        for table in ("asks", "attempts", "gaps"):
            conn.execute(f"DELETE FROM {table} WHERE student_id LIKE ?", (like,))
        conn.executemany(
            "INSERT INTO asks (student_id, question, snippet_id, status, score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            asks,
        )
        conn.executemany(
            "INSERT INTO attempts (student_id, snippet_id, question_index, chosen, correct, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            attempts,
        )
        conn.executemany(
            "INSERT INTO gaps (question, student_id, lecture_hint, created_at, resolved) VALUES (?, ?, ?, ?, ?)",
            gaps,
        )
    return {"asks": len(asks), "attempts": len(attempts), "gaps": len(gaps), "hard_snippet": hard_id}
