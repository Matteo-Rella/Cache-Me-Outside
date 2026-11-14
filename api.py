"""Rewind HTTP API (FastAPI) for the web front end in web/.

Wraps rewind.search, rewind.store, rewind.quiz and rewind.lectures; the contract is in
docs/round2-prompts.md. Inputs: JSON requests from the browser. Outputs: JSON responses with
"public" snippets only (no answer indexes, explanations or full transcript text), local video
files under /media with range support, and the static site from web/ at /.

Modes:
- real: rewind.search (embeddings + Anthropic coverage check), SQLite at config.DB_PATH.
- fake: REWIND_FAKE=1, or the real search can't start (e.g. no API key). Uses the keyword
  search in tests/fakes.py and the real SQLite store on a separate file, data/rewind-fake.db
  (the fake store in tests/fakes.py has no lecturer functions).

Run: uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rewind import config, lectures, quiz, store

WEB_DIR = config.ROOT_DIR / "web"
FAKE_DB_PATH = config.DATA_DIR / "rewind-fake.db"
MEDIA_EXTENSIONS = {".mp4", ".webm", ".m4v", ".mov", ".mkv", ".ogg", ".ogv", ".mp3", ".m4a", ".wav", ".vtt"}

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtube-nocookie.com"}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ------------------------------------------------------------------ helpers

def youtube_id(url: str | None) -> str | None:
    """Extract the 11-char video id from a YouTube URL, or None."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    else:
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v"):
            candidate = parts[1]
    return candidate if candidate and _YOUTUBE_ID.match(candidate) else None


def public_snippet(s: dict) -> dict:
    """Snippet fields safe for the browser: no answers, explanations or full text."""
    return {
        "id": s["id"],
        "lecture_id": s["lecture_id"],
        "concept": s["concept"],
        "start": s["start"],
        "end": s["end"],
        "summary": s.get("summary", ""),
        "questions": [{"q": q["q"], "options": list(q["options"])} for q in s.get("questions", [])],
    }


def lecture_info(lecture_id: str) -> dict | None:
    try:
        meta = lectures.load_meta(lecture_id)
    except (FileNotFoundError, OSError, ValueError):
        return None
    url = meta.get("video_url")
    yid = youtube_id(url)
    return {
        "lecture_id": lecture_id,
        "title": meta.get("title", lecture_id),
        "course": meta.get("course", ""),
        "lecture_number": meta.get("lecture_number"),
        "video_url": url,
        "video_type": "youtube" if yid else "file",
        "youtube_id": yid,
        "snippet_count": len(lectures.load_snippets(lecture_id)),
    }


def _with_lecture(s: dict, cache: dict) -> dict:
    lid = s["lecture_id"]
    if lid not in cache:
        cache[lid] = lecture_info(lid)
    return {"snippet": public_snippet(s), "lecture": cache[lid]}


def _llm_available() -> bool | None:
    """llm.available() if the module has it (threshold-only search fallback), else None."""
    from rewind import llm

    fn = getattr(llm, "available", None)
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def _real_search_problem() -> str | None:
    """Why the real search can't run, or None if it can.

    Once rewind.llm.available() exists, search works without an API key (threshold only),
    so no key is needed for real mode. Before that, real search requires the key and model.
    """
    try:
        import rewind.search  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - any startup failure means fake mode
        return f"{type(exc).__name__}: {exc}"
    if _llm_available() is not None:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "ANTHROPIC_API_KEY is not set"
    try:
        config.get_model()
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def _fake_search():
    from tests import fakes

    return fakes.search


def _fake_is_homework(question: str) -> bool:
    # Fake mode never calls the LLM: use only quiz's keyword pre-check.
    return bool(quiz._HOMEWORK_KEYWORDS.search(question) or quiz._NUMBERED_PROBLEM.search(question))


# ------------------------------------------------------------------ request bodies

class AskBody(BaseModel):
    student_id: str
    question: str


class AnswerBody(BaseModel):
    student_id: str
    snippet_id: str
    question_index: int
    chosen: int


# ------------------------------------------------------------------ app

def create_app(fake: bool | None = None, db_path: Path | str | None = None) -> FastAPI:
    """Build the app. fake=None decides from REWIND_FAKE and whether real search can start."""
    reason = None
    if fake is None:
        if os.environ.get("REWIND_FAKE", "").strip() == "1":
            fake, reason = True, "REWIND_FAKE=1"
        else:
            reason = _real_search_problem()
            fake = reason is not None
    if fake:
        search = _fake_search()
        is_homework = _fake_is_homework
        db = db_path if db_path is not None else FAKE_DB_PATH
    else:
        from rewind import search  # noqa: F811

        llm_ok = _llm_available()
        # Without an LLM, only quiz's keyword check runs (no API call per question).
        is_homework = _fake_is_homework if llm_ok is False else quiz.is_homework
        db = db_path  # None -> config.DB_PATH
        if llm_ok is False:
            reason = "no LLM: threshold-only search, keyword homework check"
    mode = "fake" if fake else "real"
    print(f"Rewind API mode: {mode}" + (f" ({reason})" if reason else ""), flush=True)

    st =SimpleNamespace(
        log_ask=lambda *a: store.log_ask(*a, db_path=db),
        log_attempt=lambda *a: store.log_attempt(*a, db_path=db),
        flag_gap=lambda *a: store.flag_gap(*a, db_path=db),
        resolve_gap=lambda gap_id: store.resolve_gap(gap_id, db_path=db),
        student_weak_spots=lambda sid: store.student_weak_spots(sid, db_path=db),
        lecturer_stats=lambda lid=None: store.lecturer_stats(lid, db_path=db),
        open_gaps=lambda: store.open_gaps(db_path=db),
        ask_totals=lambda: store.ask_totals(db_path=db),
        seed_demo_data=lambda: store.seed_demo_data(db_path=db),
    )

    app = FastAPI(title="Rewind API")
    app.state.mode = mode
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": mode}

    @app.get("/api/lectures")
    def get_lectures():
        return [info for lid in lectures.list_lectures() if (info := lecture_info(lid))]

    @app.get("/api/examples")
    def examples():
        templates = ["What is {c}?", "How does {c} work?", "Can you explain {c}?"]
        seen, out = set(), []
        for s in lectures.all_snippets():
            concept = s.get("concept", "").strip()
            if concept and concept.lower() not in seen:
                seen.add(concept.lower())
                out.append(templates[len(out) % len(templates)].format(c=concept))
            if len(out) == 3:
                break
        return out

    @app.post("/api/ask")
    def ask(body: AskBody):
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "Question is empty.")
        try:
            result = search.answer(question)
        except Exception as exc:  # noqa: BLE001 - surface API/config errors, don't flag a fake gap
            raise HTTPException(503, f"Search is unavailable: {exc}") from exc
        st.log_ask(body.student_id, question, result)
        homework = is_homework(question)
        cache: dict = {}
        if result.get("status") != "found" or not result.get("best"):
            st.flag_gap(body.student_id, question)
            return {"status": "not_covered", "homework": homework, "snippet": None,
                    "lecture": None, "alternatives": []}
        best = result["best"]
        best = best.get("snippet", best) if "id" not in best else best
        main = _with_lecture(best, cache)
        alts = [_with_lecture(a.get("snippet", a) if "id" not in a else a, cache)
                for a in result.get("alternatives", [])]
        return {"status": "found", "homework": homework, "snippet": main["snippet"],
                "lecture": main["lecture"], "alternatives": alts}

    @app.post("/api/answer")
    def answer(body: AnswerBody):
        snippet = lectures.get_snippet(body.snippet_id)
        if snippet is None:
            raise HTTPException(404, "Unknown snippet.")
        questions = snippet.get("questions", [])
        if not 0 <= body.question_index < len(questions):
            raise HTTPException(400, "Invalid question_index.")
        if not 0 <= body.chosen < len(questions[body.question_index]["options"]):
            raise HTTPException(400, "Invalid option.")
        graded = quiz.grade(snippet, body.question_index, body.chosen)
        st.log_attempt(body.student_id, body.snippet_id, body.question_index, body.chosen, graded["correct"])
        return {"correct": bool(graded["correct"]),
                "correct_index": int(questions[body.question_index]["answer"]),
                "explanation": graded["explanation"]}

    @app.get("/api/students/{student_id}/rewatch")
    def rewatch(student_id: str):
        cache: dict = {}
        out = []
        for sid in st.student_weak_spots(student_id):
            s = lectures.get_snippet(sid)
            if s is not None:
                out.append(_with_lecture(s, cache))
        return out

    @app.get("/api/lecturer/stats")
    def lecturer_stats():
        rows = st.lecturer_stats()
        totals = st.ask_totals()
        n = totals["asks"]
        tried = [r["fail_rate"] for r in rows if r["attempts"]]
        titles: dict = {}
        snippets = []
        for r in rows:
            lid = r["lecture_id"]
            if lid not in titles:
                info = lecture_info(lid)
                titles[lid] = info["title"] if info else lid
            snippets.append({
                "snippet_id": r["snippet_id"], "concept": r["concept"], "lecture_id": lid,
                "lecture_title": titles[lid], "start": r["start"], "end": r["end"],
                "times_asked": r["times_asked"], "attempts": r["attempts"], "fail_rate": r["fail_rate"],
            })
        return {
            "totals": {
                "asks": n,
                "found_pct": round(100 * totals["found"] / n, 1) if n else 0.0,
                "not_covered_pct": round(100 * totals["not_covered"] / n, 1) if n else 0.0,
                "avg_fail_rate": round(sum(tried) / len(tried), 3) if tried else 0.0,
            },
            "snippets": snippets,
        }

    @app.get("/api/lecturer/gaps")
    def gaps():
        return [{"gap_id": g["gap_ids"][0], "question": g["question"], "count": g["count"],
                 "last_asked": g["last_asked"]} for g in st.open_gaps()]

    @app.post("/api/lecturer/gaps/{gap_id}/resolve")
    def resolve(gap_id: int):
        if not st.resolve_gap(gap_id):
            raise HTTPException(404, "Unknown gap.")
        return {"ok": True}

    @app.post("/api/lecturer/seed")
    def seed():
        st.seed_demo_data()
        return {"ok": True}

    @app.get("/api/graph")
    def graph():
        stats = {r["snippet_id"]: r for r in st.lecturer_stats()}
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set = set()

        def edge(a: str, b: str) -> None:
            if (a, b) not in seen_edges:
                seen_edges.add((a, b))
                edges.append({"from": a, "to": b})

        for lid in lectures.list_lectures():
            info = lecture_info(lid)
            if info is None:
                continue
            course = info["course"] or "Uncategorised"
            cid = f"course:{course.lower()}"
            nodes.setdefault(cid, {"id": cid, "label": course, "type": "course"})
            lnode = f"lecture:{lid}"
            nodes[lnode] = {"id": lnode, "label": info["title"], "type": "lecture"}
            edge(cid, lnode)
            for s in lectures.load_snippets(lid):
                r = stats.get(s["id"], {})
                snode = f"snippet:{s['id']}"
                nodes[snode] = {"id": snode, "label": s["concept"], "type": "snippet", "snippet_id": s["id"],
                                "times_asked": r.get("times_asked", 0), "fail_rate": r.get("fail_rate", 0.0)}
                edge(lnode, snode)
                key = " ".join(s["concept"].lower().split())
                knode = f"concept:{key}"
                nodes.setdefault(knode, {"id": knode, "label": s["concept"], "type": "concept"})
                edge(snode, knode)
        return {"nodes": list(nodes.values()), "edges": edges}

    @app.get("/media/{lecture_id}/{filename}")
    def media(lecture_id: str, filename: str):
        if not _SAFE_NAME.match(lecture_id) or not _SAFE_NAME.match(filename):
            raise HTTPException(404, "Not found.")
        if Path(filename).suffix.lower() not in MEDIA_EXTENSIONS:
            raise HTTPException(404, "Not found.")
        base = config.LECTURES_DIR.resolve()
        path = (base / lecture_id / filename).resolve()
        if not path.is_relative_to(base / lecture_id) or not path.is_file():
            raise HTTPException(404, "Not found.")
        return FileResponse(path)  # Starlette handles Range requests (206)

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
