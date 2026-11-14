"""SQLite store for student activity: asks, quiz attempts, coverage gaps and follow-up chat.

Uses only the standard-library sqlite3 module. The database lives at config.DB_PATH
(data/rewind.db) and is created on first use. Every public function takes an optional
`db_path` so tests can point at a temporary file.

Inputs: student ids, questions, results from search.answer, quiz attempts.
Outputs:
- student_weak_spots(student_id) -> list[str] snippet ids, newest first
- lecturer_stats(lecture_id=None, include_hidden=False) -> list[{"snippet_id", "concept", "lecture_id", "start", "end",
  "times_asked", "attempts", "fails", "fail_rate", "followups"}]
- open_gaps() -> list[{"question", "count", "gap_ids", "student_ids", "lecture_hint",
  "first_asked", "last_asked"}]
- ask_totals() -> {"asks", "found", "not_covered", "slide_only"}
- resolve_gap(gap_id) -> bool
- log_chat(student_id, snippet_id, role, content, outside_materials=False) -> row id
- chat_history(student_id, snippet_id) -> list[{"role", "content", "outside_materials", "created_at"}]
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
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    snippet_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    outside_materials INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_snippet ON chat_messages(snippet_id, role);
CREATE INDEX IF NOT EXISTS idx_chat_student ON chat_messages(student_id, snippet_id);
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
    """Counts over all asks: {"asks", "found", "not_covered", "slide_only"}."""
    with _connect(db_path) as conn:
        r = conn.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(SUM(status = 'found'), 0) AS found, "
            "COALESCE(SUM(status = 'not_covered'), 0) AS nc, "
            "COALESCE(SUM(status = 'slide_only'), 0) AS so FROM asks"
        ).fetchone()
    return {"asks": r["n"], "found": r["found"], "not_covered": r["nc"], "slide_only": r["so"]}


CHAT_ROLES = ("user", "assistant")


def log_chat(
    student_id: str,
    snippet_id: str,
    role: str,
    content: str,
    outside_materials: bool = False,
    db_path: Path | str | None = None,
) -> int:
    """Record one follow-up chat message under a clip. role is "user" or "assistant".
    Returns the new row id. A "user" message counts as one follow-up for that snippet.
    """
    if role not in CHAT_ROLES:
        raise ValueError(f"role must be one of {CHAT_ROLES}, not {role!r}")
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO chat_messages (student_id, snippet_id, role, content, outside_materials, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, snippet_id, role, content, 1 if outside_materials else 0, _now()),
        )
        return cur.lastrowid


def chat_history(student_id: str, snippet_id: str, db_path: Path | str | None = None) -> list[dict]:
    """A student's chat under one clip, oldest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT role, content, outside_materials, created_at FROM chat_messages "
            "WHERE student_id = ? AND snippet_id = ? ORDER BY created_at, id",
            (student_id, snippet_id),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"], "outside_materials": bool(r["outside_materials"]),
             "created_at": r["created_at"]} for r in rows]


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


def _hidden(lecture_id: str) -> bool:
    """True if the lecture's meta.json has "hidden": true (test fixtures such as demo01)."""
    try:
        from rewind import lectures

        return lectures.load_meta(lecture_id).get("hidden") is True
    except Exception:  # noqa: BLE001 - unreadable meta: treat as visible
        return False


def _snippet_info(include_hidden: bool = True) -> dict[str, dict]:
    try:
        from rewind import lectures

        lecture_ids = [lid for lid in lectures.list_lectures() if include_hidden or not _hidden(lid)]
        return {s["id"]: s for lid in lecture_ids for s in lectures.load_snippets(lid)}
    except Exception:  # noqa: BLE001
        return {}


def lecturer_stats(
    lecture_id: str | None = None,
    db_path: Path | str | None = None,
    include_hidden: bool = False,
) -> list[dict]:
    """Per snippet: times asked, quiz attempts, fail rate, follow-ups (student chat messages under
    the clip). Includes snippets from the lecture
    files that have no activity yet. Hidden lectures are left out unless include_hidden.
    Sorted by times_asked desc, then fail_rate desc.
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
        followups = {
            r["snippet_id"]: r["n"]
            for r in conn.execute(
                "SELECT snippet_id, COUNT(*) AS n FROM chat_messages WHERE role = 'user' GROUP BY snippet_id"
            )
        }

    info = _snippet_info(include_hidden=True)
    hidden: dict[str, bool] = {}
    rows = []
    for sid in set(asked) | set(tried) | set(followups) | set(info):
        snip = info.get(sid, {})
        lid = snip.get("lecture_id") or sid.rsplit("-", 1)[0]
        if lecture_id is not None and lid != lecture_id:
            continue
        if not include_hidden:
            if lid not in hidden:
                hidden[lid] = _hidden(lid)
            if hidden[lid]:
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
                "followups": followups.get(sid, 0),
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

# Gap questions (not covered by the lectures) with the lecture they're closest to.
_DEMO_GAPS = [
    ("What is price elasticity?", "econ01"),
    ("How do you calculate consumer surplus?", "econ01"),
    ("How does memory work in the brain?", "psych02"),
    ("What did Freud say about the unconscious?", "psych02"),
    ("Is the split-brain experiment on the exam?", None),
]

# Concepts (lower-case prefix) that make a believable "hard" snippet; else the middle snippet.
_HARD_PREFERENCES = ("positive vs. normative", "split brain", "all-or-nothing firing", "opportunity cost")

_ASK_TEMPLATES = [
    "what does {c} mean",
    "can you explain {c} again",
    "I didn't get {c}",
    "{c}?",
    "where does the professor talk about {c}",
]

_CHAT_QUESTIONS = [
    "Can you give me an example of {c}?",
    "Why does {c} matter here?",
    "I still don't get {c}. Can you explain it more simply?",
    "How is {c} different from what he said just before?",
    "Is {c} something we need to know for the exam?",
]
_CHAT_ANSWER = "He explains this at [{t}]: {summary}"
_CHAT_OUTSIDE = [
    ("Does this work the same way in other countries?",),
    ("Is there a formula for this that we should memorise?",),
]
_CHAT_OUTSIDE_ANSWER = "This isn't in your course materials. I've flagged it to your lecturer."

DEMO_CHAT_PAIRS = 15  # student message + reply = ~30 chat messages
HARD_CHAT_PAIRS = 4  # per hard snippet, so they top "Most follow-ups"

DEMO_ASKS = 54
DEMO_ATTEMPTS = 110
HARD_ATTEMPTS = 22  # per hard snippet
HARD_P_CORRECT = 0.3
NORMAL_P_CORRECT = 0.85
NORMAL_MAX_FAIL_RATE = 1 / 3


def _pick_hard(snippets: list[dict]) -> dict:
    for prefix in _HARD_PREFERENCES:
        for s in snippets:
            if s["concept"].lower().startswith(prefix):
                return s
    return snippets[len(snippets) // 2]


def seed_demo_data(db_path: Path | str | None = None, seed: int = 42) -> dict:
    """Fill the DB with realistic fake activity on every visible lecture (hidden ones such as the
    demo01 fixture are skipped): 10 students, ~60 asks, ~110 attempts, one clearly "hard"
    snippet per lecture (high fail rate; every other snippet stays at or under 1/3),
    5 gap questions (two asked twice). Idempotent: previous demo rows are cleared first.
    Demo student ids start with "demo-". Returns counts and {"hard_snippets": [ids]}.
    """
    rng = random.Random(seed)
    by_lecture: dict[str, list[dict]] = {}
    for s in sorted(_snippet_info(include_hidden=False).values(), key=lambda s: s["id"]):
        if s.get("questions"):
            by_lecture.setdefault(s["lecture_id"], []).append(s)
    if not by_lecture:
        raise RuntimeError("No visible lectures with snippets under data/lectures; can't seed demo data.")
    snippets = [s for lid in sorted(by_lecture) for s in by_lecture[lid]]
    hard_ids = [_pick_hard(by_lecture[lid])["id"] for lid in sorted(by_lecture)]
    normal = [s for s in snippets if s["id"] not in hard_ids]

    students = [f"{DEMO_PREFIX}{name}" for name in
                ("giulia", "marco", "sofia", "luca", "anna", "matteo", "chiara", "davide", "elena", "tom")]
    start = datetime.now(timezone.utc) - timedelta(days=14)

    def ts() -> str:
        return (start + timedelta(minutes=rng.randint(0, 14 * 24 * 60), seconds=rng.random())).isoformat(
            timespec="microseconds"
        )

    def attempt(snip: dict, p_correct: float) -> list:
        qi = rng.randrange(len(snip["questions"]))
        answer = snip["questions"][qi]["answer"]
        correct = rng.random() < p_correct
        n_opts = len(snip["questions"][qi]["options"])
        chosen = answer if correct else rng.choice([i for i in range(n_opts) if i != answer])
        return [rng.choice(students), snip["id"], qi, chosen, int(correct), ts(), answer]

    asks, attempts, gaps = [], [], []
    weights = [8 if s["id"] in hard_ids else 1 for s in snippets]
    for _ in range(DEMO_ASKS):
        snip = rng.choices(snippets, weights=weights)[0]
        q = rng.choice(_ASK_TEMPLATES).format(c=snip["concept"].lower())
        asks.append((rng.choice(students), q, snip["id"], "found", round(rng.uniform(0.45, 0.85), 3), ts()))

    by_id = {s["id"]: s for s in snippets}
    for hid in hard_ids:
        attempts += [attempt(by_id[hid], HARD_P_CORRECT) for _ in range(HARD_ATTEMPTS)]
    if normal:
        attempts += [attempt(rng.choice(normal), NORMAL_P_CORRECT)
                     for _ in range(max(0, DEMO_ATTEMPTS - len(attempts)))]
    # Keep every normal snippet clearly easier than the hard ones: turn surplus fails into passes.
    for sid in {a[1] for a in attempts} - set(hard_ids):
        rows = [a for a in attempts if a[1] == sid]
        fails = [a for a in rows if not a[4]]
        while fails and len(fails) / len(rows) > NORMAL_MAX_FAIL_RATE:
            a = fails.pop()
            a[3], a[4] = a[6], 1
            fails = [r for r in rows if not r[4]]
    attempts = [tuple(a[:6]) for a in attempts]

    chats = []

    def chat_pair(snip: dict, outside: bool = False) -> None:
        student, when = rng.choice(students), datetime.fromisoformat(ts())
        if outside:
            question, answer = rng.choice(_CHAT_OUTSIDE)[0], _CHAT_OUTSIDE_ANSWER
        else:
            concept = snip["concept"]
            c = concept if concept[:1].isupper() and concept[1:2].isupper() else concept[:1].lower() + concept[1:]
            question = rng.choice(_CHAT_QUESTIONS).format(c=c)
            m, s = divmod(int(snip["start"]), 60)
            answer = _CHAT_ANSWER.format(t=f"{m:02d}:{s:02d}", summary=snip.get("summary", "").split(". ")[0].rstrip(".") + ".")
        later = when + timedelta(seconds=rng.randint(3, 12))
        chats.append((student, snip["id"], "user", question, 0, when.isoformat(timespec="microseconds")))
        chats.append((student, snip["id"], "assistant", answer, int(outside), later.isoformat(timespec="microseconds")))

    for hid in hard_ids:
        for _ in range(HARD_CHAT_PAIRS):
            chat_pair(by_id[hid])
    pool = normal or snippets
    for i in range(max(0, DEMO_CHAT_PAIRS - len(chats) // 2)):
        chat_pair(rng.choice(pool), outside=i < len(_CHAT_OUTSIDE))

    visible = set(by_lecture)
    gap_rows =_DEMO_GAPS + [_DEMO_GAPS[0], (_DEMO_GAPS[2][0].lower(), _DEMO_GAPS[2][1])]
    for question, hint in gap_rows:
        student, when = rng.choice(students), ts()
        gaps.append((question, student, hint if hint in visible else None, when, 0))
        asks.append((student, question, None, "not_covered", round(rng.uniform(0.1, 0.3), 3), when))

    with _connect(db_path) as conn:
        like = DEMO_PREFIX + "%"
        for table in ("asks", "attempts", "gaps", "chat_messages"):
            conn.execute(f"DELETE FROM {table} WHERE student_id LIKE ?", (like,))
        conn.executemany(
            "INSERT INTO chat_messages (student_id, snippet_id, role, content, outside_materials, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            chats,
        )
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
    return {"asks": len(asks), "attempts": len(attempts), "gaps": len(gaps), "chat_messages": len(chats),
            "hard_snippets": hard_ids}
