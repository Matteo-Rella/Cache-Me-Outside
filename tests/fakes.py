"""Fake search and store for developing the student app before the real modules are merged.

Enable in the app with REWIND_FAKE=1. `search` and `store` mimic the real modules' function names.
The fake store keeps state in module globals (lives as long as the Streamlit process).
"""
from __future__ import annotations

import re
import types

from rewind import lectures

_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def _search(question: str) -> list[dict]:
    q = _tokens(question)
    scored = []
    for s in lectures.all_snippets():
        doc = _tokens(" ".join([s["concept"], s["summary"], s["text"]]))
        score = len(q & doc) / max(len(q), 1)
        scored.append({"snippet": s, "score": score})
    return sorted(scored, key=lambda r: r["score"], reverse=True)


def _answer(question: str) -> dict:
    ranked = _search(question)
    if not ranked or ranked[0]["score"] < 0.3:
        return {"status": "not_covered", "best": None, "alternatives": [], "score": ranked[0]["score"] if ranked else 0.0}
    return {
        "status": "found",
        "best": ranked[0]["snippet"],
        "alternatives": [r["snippet"] for r in ranked[1:3]],
        "score": ranked[0]["score"],
    }


search = types.SimpleNamespace(answer=_answer, search=lambda q, top_k=3: _search(q)[:top_k])

_asks: list[dict] = []
_attempts: list[dict] = []
_gaps: list[dict] = []


def _weak_spots(student_id: str) -> list[str]:
    latest: dict[str, bool] = {}
    for a in _attempts:  # chronological; later overwrites earlier
        if a["student_id"] == student_id:
            latest[a["snippet_id"]] = a["correct"]
    ordered = [a["snippet_id"] for a in reversed(_attempts) if a["student_id"] == student_id]
    seen, out = set(), []
    for sid in ordered:
        if sid not in seen:
            seen.add(sid)
            if not latest[sid]:
                out.append(sid)
    return out


store = types.SimpleNamespace(
    init_db=lambda: None,
    log_ask=lambda student_id, question, result: _asks.append(
        {"student_id": student_id, "question": question, "status": result["status"]}
    ),
    log_attempt=lambda student_id, snippet_id, question_index, chosen, correct: _attempts.append(
        {"student_id": student_id, "snippet_id": snippet_id, "question_index": question_index,
         "chosen": chosen, "correct": correct}
    ),
    flag_gap=lambda student_id, question: _gaps.append({"student_id": student_id, "question": question}),
    student_weak_spots=_weak_spots,
)
