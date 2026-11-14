"""Quiz grading and homework detection.

grade(snippet, question_index, chosen) -> {"correct": bool, "explanation": str}
is_homework(question) -> bool  (keyword pre-check, then a small LLM classification)
"""
from __future__ import annotations

import re

_HOMEWORK_KEYWORDS = re.compile(
    r"\b(solve|calculate|compute|find the value|exercise|homework|problem set|"
    r"risolvi|risolvere|calcola|calcolare|esercizio|compito)\b",
    re.IGNORECASE,
)
# A pasted numbered problem: "1)", "2.", "Problem 3", "Exercise 4" at line start.
_NUMBERED_PROBLEM = re.compile(r"(^|\n)\s*(\d{1,2}[.)]|\(\d{1,2}\)|problem\s+\d+|exercise\s+\d+)\s+\S", re.IGNORECASE)

HOMEWORK_PROMPT = (
    "You classify student questions for a lecture-video tool. Decide whether the message asks "
    "for the solution to a specific homework, exam or exercise problem (a concrete task to "
    "solve, calculate or prove), as opposed to a conceptual question about a topic. "
    'Reply with only JSON: {"homework": true} or {"homework": false}.'
)


def grade(snippet: dict, question_index: int, chosen: int) -> dict:
    """Grade one answer. Returns {"correct": bool, "explanation": str}."""
    q = snippet["questions"][question_index]
    return {"correct": int(chosen) == int(q["answer"]), "explanation": q["explanation"]}


def is_homework(question: str) -> bool:
    """True if the question looks like a homework/exam task rather than a concept question."""
    if _HOMEWORK_KEYWORDS.search(question) or _NUMBERED_PROBLEM.search(question):
        return True
    try:
        from rewind import llm

        if not llm.available():
            return False
        result = llm.ask_json(HOMEWORK_PROMPT, question, max_tokens=50)
        return bool(result.get("homework")) if isinstance(result, dict) else False
    except Exception:
        # Classification is a safety net; if the API is unavailable, treat as a normal question.
        return False
