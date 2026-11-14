"""Rewind HTTP API (FastAPI) for the web front end in web/.

Wraps rewind.search, rewind.store, rewind.quiz, rewind.lectures, rewind.settings, rewind.llm and
rewind.context_package; contracts in docs/round2-prompts.md and docs/round4-prompts.md.
Inputs: JSON requests from the browser. Outputs: JSON responses with "public" snippets only
(no answer indexes, explanations or full transcript text), local video files under /media with
range support, and the static site from web/ at /.

Modes:
- real: rewind.search (embeddings, plus the LLM coverage check when llm.available()),
  SQLite at config.DB_PATH. The search model and indexes load in a background thread at
  startup; /api/health reports status "warming" until they're ready.
- fake: REWIND_FAKE=1, or rewind.search can't be imported. Uses the keyword search in
  tests/fakes.py and the real SQLite store on data/rewind-fake.db.

Lectures with "hidden": true in meta.json (e.g. the demo01 test fixture) are left out of
/api/lectures, /api/examples, /api/ask results and /api/graph.

AI settings, context package, import and generate endpoints, and web/settings.html, answer only
requests from loopback addresses (403 otherwise), since the server may run on 0.0.0.0 for phones.

Run: uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import importlib
import ipaddress
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from rewind import config, ingest, lectures, quiz, store

WEB_DIR = config.ROOT_DIR / "web"
FAKE_DB_PATH = config.DATA_DIR / "rewind-fake.db"
MEDIA_EXTENSIONS = {".mp4", ".webm", ".m4v", ".mov", ".mkv", ".ogg", ".ogv", ".mp3", ".m4a", ".wav", ".vtt"}
LOCAL_ONLY_PAGES = ("settings.html",)
MAX_IMPORT_CHARS = 5_000_000
SLIDE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_SLIDES_BYTES = 50 * 1024 * 1024  # matches rewind.slides.MAX_SOURCE_BYTES
MAX_CHAT_MESSAGES = 10  # the browser sends the last 10; anything older is dropped server-side too
MAX_CHAT_CHARS = 4000
MAX_ASK_SLIDES = 3

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
    """Snippet fields safe for the browser: no answers, explanations or full text.
    Includes the aligned slide numbers ("slides") when the snippet has them."""
    out = {
        "id": s["id"],
        "lecture_id": s["lecture_id"],
        "concept": s["concept"],
        "start": s["start"],
        "end": s["end"],
        "summary": s.get("summary", ""),
        "questions": [{"q": q["q"], "options": list(q["options"])} for q in s.get("questions", [])],
    }
    if isinstance(s.get("slides"), list):
        out["slides"] = [int(n) for n in s["slides"] if isinstance(n, int) or str(n).isdigit()]
    return out


# ------------------------------------------------------------------ slides

def load_slides(lecture_id: str) -> list[dict]:
    """slides.json for a lecture ([] if none): rewind.slides.load_slides when it exists, else the file."""
    try:
        from rewind import slides as slides_mod

        fn = getattr(slides_mod, "load_slides", None)
    except ImportError:
        fn = None
    if callable(fn):
        try:
            return list(fn(lecture_id) or [])
        except FileNotFoundError:
            return []
    path = config.LECTURES_DIR / lecture_id / "slides" / "slides.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def slide_image_url(lecture_id: str, slide: dict) -> str | None:
    """'media/<id>/slides/<file>' for the slide's image, or None. Only plain slides/<file> paths."""
    image = str(slide.get("image") or "").replace("\\", "/")
    parts = image.split("/")
    if len(parts) != 2 or parts[0] != "slides" or not _SAFE_NAME.match(parts[1]):
        return None
    return f"media/{lecture_id}/slides/{parts[1]}"


def public_slide(lecture_id: str, slide: dict) -> dict:
    return {
        "lecture_id": lecture_id,
        "n": slide.get("n"),
        "title": slide.get("title", ""),
        "text": slide.get("text", ""),
        "image_url": slide_image_url(lecture_id, slide),
    }


def _has_slides(lecture_id: str) -> bool:
    return (config.LECTURES_DIR / lecture_id / "slides" / "slides.json").is_file()


def _load_meta(lecture_id: str) -> dict | None:
    try:
        return lectures.load_meta(lecture_id)
    except (FileNotFoundError, OSError, ValueError):
        return None


def is_hidden(lecture_id: str) -> bool:
    meta = _load_meta(lecture_id)
    return bool(meta and meta.get("hidden") is True)


def visible_lectures() -> list[str]:
    return [lid for lid in lectures.list_lectures() if not is_hidden(lid)]


def lecture_info(lecture_id: str) -> dict | None:
    meta = _load_meta(lecture_id)
    if meta is None:
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


def _as_snippet(item: Any) -> dict | None:
    """search results may hold the snippet dict itself or {"snippet": ..., "score": ...}."""
    if not isinstance(item, dict):
        return None
    if "id" in item:
        return item
    inner = item.get("snippet")
    return inner if isinstance(inner, dict) else None


def is_loopback(host: str | None) -> bool:
    """True for 127.0.0.0/8, ::1 and IPv4-mapped loopback. Anything unparseable is not local."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def local_only(request: Request) -> None:
    """Dependency: 403 unless the TCP peer is loopback. Forwarded headers are ignored on purpose."""
    if not is_loopback(request.client.host if request.client else None):
        raise HTTPException(403, "This is only available on the computer running Rewind.")


def _llm_available() -> bool | None:
    """llm.available() if the module has it, else None. Errors count as not available."""
    try:
        from rewind import llm
    except Exception:  # noqa: BLE001
        return False
    fn = getattr(llm, "available", None)
    if not callable(fn):
        return None
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False


def _optional(module: str, func: str) -> Callable:
    """rewind.<module>.<func>, or raise 501 if another terminal hasn't built it yet."""
    full = f"rewind.{module}"
    try:
        mod = importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name == full:
            raise HTTPException(501, f"{full} isn't available yet, so {func}() can't run.") from None
        raise
    fn = getattr(mod, func, None)
    if not callable(fn):
        raise HTTPException(501, f"{full}.{func}() isn't available yet.")
    return fn


def _mask(secret: str) -> str:
    return "…" + secret[-4:] if len(secret) > 8 else "…"


def scrub_secret(obj: Any, secret: str | None) -> Any:
    """Defence in depth: replace any string containing the raw key with a mask."""
    if isinstance(obj, dict):
        return {k: scrub_secret(v, secret) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_secret(v, secret) for v in obj]
    if isinstance(obj, str):
        keys = {secret or "", *(os.environ.get(n, "") for n in
                ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"))}
        for key in sorted(filter(None, keys), key=len, reverse=True):
            obj = obj.replace(key, _mask(key))
    return obj


def _raw_key() -> str | None:
    try:
        key = _optional("settings", "load")().get("api_key")
    except HTTPException:
        key = None
    except Exception:  # noqa: BLE001
        key = None
    env_keys = [os.environ.get(n, "") for n in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY")]
    return key or next((k for k in env_keys if k.strip()), None)


def _public_settings() -> dict:
    data = _optional("settings", "public")()
    secret = _raw_key()
    data = scrub_secret(data, secret)
    # Some providers' keys are short or env-only; never let a full key through under api_key.
    if isinstance(data, dict) and secret and data.get("api_key") == secret:
        data["api_key"] = _mask(secret)
    return data


# ------------------------------------------------------------------ example questions

_EXAMPLE_MAX_WORDS = 4
_EXAMPLE_SKIP = re.compile(r"[:;,?!()'’\"]|\bvs\.?\b|\band\b|\bor\b", re.IGNORECASE)
_QUESTION_START = re.compile(r"^(what|how|why|when|where|who|which|is|are|do|does|can)\b", re.IGNORECASE)
_PREPOSITIONS = {"as", "of", "on", "in", "for", "with", "from", "to", "like", "about", "at", "by"}
# Head nouns read naturally without "the": "What is opportunity cost?", "What is dualism?"
_MASS_NOUNS = {
    "cost", "analysis", "information", "behaviour", "behavior", "economics", "consciousness",
    "equilibrium", "knowledge", "demand", "supply", "memory", "learning", "research", "evidence",
    "language", "perception", "attention", "money", "trade", "welfare", "utility", "inflation",
    "psychology", "growth", "power", "control", "risk", "income", "capital", "labor", "labour",
}
_MASS_SUFFIXES = ("ism", "ics", "ness", "ity", "tion", "sion", "ment", "ance", "ence", "ing", "ogy", "phy")


def _is_proper_or_acronym(word: str, context: str) -> bool:
    """Acronyms (GDP, L-DOPA) and words the lecture text writes capitalised mid-sentence (Descartes)."""
    letters = [c for c in word if c.isalpha()]
    if sum(c.isupper() for c in letters) >= 2 or (letters and not letters[0].isupper() and any(c.isupper() for c in letters)):
        return True
    if not letters or not letters[0].isupper():
        return False
    upper = lower = 0
    for m in re.finditer(rf"(?<![\w-]){re.escape(word)}(?![\w-])", context, re.IGNORECASE):
        before = context[: m.start()].rstrip()
        if not before or before[-1] in ".!?\"“":
            continue  # sentence start: capital letter says nothing
        if m.group(0)[0].isupper():
            upper += 1
        else:
            lower += 1
    return upper > 0 and upper > lower


def example_question(concept: str, context: str = "") -> str | None:
    """A natural student question for a short concept, or None if the concept doesn't make one.

    "Opportunity cost" -> "What is opportunity cost?"; "Demand curve" -> "What is the demand curve?";
    "Neural networks" -> "What are neural networks?". Concepts that are long, contain punctuation,
    "and"/"vs", prepositions or are already questions are skipped.
    """
    concept = " ".join(concept.split())
    words = concept.split(" ")
    if not concept or len(words) > _EXAMPLE_MAX_WORDS or _EXAMPLE_SKIP.search(concept):
        return None
    if _QUESTION_START.match(concept) or any(w.lower() in _PREPOSITIONS for w in words[1:]):
        return None
    if any(w[:1].isupper() for w in words[1:]) and not all(_is_proper_or_acronym(w, context) for w in words[1:] if w[:1].isupper()):
        return None  # Title Case headings ("The Astonishing Hypothesis") read badly as questions
    article = None
    if words[0].lower() == "the" and len(words) > 1:
        words = words[1:]
        article = "the "
    proper = _is_proper_or_acronym(words[0], context)
    acronym = sum(c.isupper() for c in words[0]) >= 2
    if proper and not acronym and len(words) == 1:
        return None  # a bare name ("Freud") would need "Who", not "What is"
    if not proper:
        words[0] = words[0][:1].lower() + words[0][1:]
    phrase = " ".join(words)
    head = words[-1].lower() if words else ""
    plural = head.endswith("s") and not head.endswith(("ss", "is", "us", "ics"))
    # A name only drops the article when it is the head ("GDP", "Adam Smith"), not a modifier ("Broca area").
    head_is_name = _is_proper_or_acronym(words[-1], context) if words else False
    if article is None:
        if head_is_name or plural or head in _MASS_NOUNS or head.endswith(_MASS_SUFFIXES):
            article = ""
        else:
            article = "the "
    verb = "are" if plural else "is"
    return f"What {verb} {article}{phrase}?"


def preset_for(provider: str | None, base_url: str | None) -> str | None:
    """Fallback for settings.public()["preset"]: "openrouter" | "ollama" | "lmstudio" | None."""
    if provider != "openai_compatible" or not base_url:
        return None
    url = base_url.lower()
    if "openrouter.ai" in url:
        return "openrouter"
    port = urlparse(url).port
    if port == 11434 or "ollama" in url:
        return "ollama"
    if port == 1234 or "lmstudio" in url or "lm-studio" in url:
        return "lmstudio"
    return None


def _is_homework_keywords(question: str) -> bool:
    # No LLM: use only quiz's keyword pre-check.
    return bool(quiz._HOMEWORK_KEYWORDS.search(question) or quiz._NUMBERED_PROBLEM.search(question))


def snippet_requests(lecture_id: str, title: str | None = None) -> list[dict]:
    """Initial generation requests for this lecture (verification/repair excluded), built by running
    request_raw_snippets with a recording `ask` (no AI call). [{"system", "user", "max_tokens"}]."""
    from rewind import snippet_maker, transcript_io

    segments = transcript_io.validate_segments(lectures.load_transcript(lecture_id))
    calls: list[dict] = []

    def record(system: str, user: str, max_tokens: int | None = None, **_: Any) -> list:
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return []

    snippet_maker.request_raw_snippets(segments, title=title, ask=record)
    return calls


def _fake_search():
    from tests import fakes

    return fakes.search


# ------------------------------------------------------------------ request bodies

class AskBody(BaseModel):
    student_id: str
    question: str


class AnswerBody(BaseModel):
    student_id: str
    snippet_id: str
    question_index: int
    chosen: int


class SettingsBody(BaseModel):
    mode: Literal["api", "package", "none"]
    provider: Literal["anthropic", "openai", "gemini", "openai_compatible"] | None = None
    model: str | None = None
    api_key: str | None = None  # omitted or empty: keep the current key
    base_url: str | None = None
    chat_policy: Literal["strict", "labelled"] | None = None
    preprocess_model: str | None = None


class ImportBody(BaseModel):
    raw_text: str = Field(max_length=MAX_IMPORT_CHARS)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CHAT_CHARS)


class ChatBody(BaseModel):
    student_id: str = Field(min_length=1, max_length=200)
    snippet_id: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=50)


def chat_status() -> tuple[bool, str]:
    """(available, reason). Chat needs a live AI (API mode, incl. local Ollama), llm.chat and rewind.chat."""
    if _llm_available() is not True:
        return False, "Chat needs an AI API. Choose \"Use an API\" in AI settings."
    try:
        from rewind import llm
    except Exception:  # noqa: BLE001
        return False, "rewind.llm can't be loaded."
    if not callable(getattr(llm, "chat", None)):
        return False, "llm.chat() isn't available yet."
    try:
        chat_mod = importlib.import_module("rewind.chat")
    except ImportError:
        return False, "rewind.chat isn't available yet."
    if not callable(getattr(chat_mod, "respond", None)):
        return False, "rewind.chat.respond() isn't available yet."
    return True, ""


def sse(event: dict) -> str:
    """One Server-Sent Event carrying one JSON object."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _with_purpose(fn: Callable, purpose: str) -> dict:
    """{"purpose": purpose} if fn accepts it, else {} (before T2's purpose support lands)."""
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "purpose" in params or any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return {"purpose": purpose}
    return {}


# ------------------------------------------------------------------ guarded static files

class WebFiles(StaticFiles):
    """web/ as static files; settings.html only for loopback clients."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        client = scope.get("client")
        if not is_loopback(client[0] if client else None) and self._is_local_only(path):
            raise HTTPException(403, "This page is only available on the computer running Rewind.")
        return await super().get_response(path, scope)

    def _is_local_only(self, path: str) -> bool:
        name = os.path.basename(os.path.normpath(path)).lower().rstrip(". ")
        if any(name.startswith(p) for p in LOCAL_ONLY_PAGES):  # also catches "settings.html::$DATA"
            return True
        full, stat = self.lookup_path(path)
        if stat is None:
            return False
        for page in LOCAL_ONLY_PAGES:
            guarded = Path(self.directory) / page
            try:
                if guarded.exists() and os.path.samefile(full, guarded):
                    return True
            except OSError:
                pass
        return False


# ------------------------------------------------------------------ app

def create_app(
    fake: bool | None = None,
    db_path: Path | str | None = None,
    warmer: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the app.

    fake=None decides from REWIND_FAKE and whether rewind.search imports. `warmer` replaces the
    startup warm-up (loading the search model and indexes) in tests.
    """
    reason = None
    search: Any = None
    if fake is None:
        if os.environ.get("REWIND_FAKE", "").strip() == "1":
            fake, reason = True, "REWIND_FAKE=1"
        else:
            try:
                from rewind import search  # noqa: F811
                fake = False
            except Exception as exc:  # noqa: BLE001 - any import failure means fake mode
                fake, reason = True, f"rewind.search failed to import: {type(exc).__name__}: {exc}"
    if fake:
        search = _fake_search()
        db = db_path if db_path is not None else FAKE_DB_PATH
    else:
        if search is None:
            from rewind import search  # noqa: F811
        db = db_path  # None -> config.DB_PATH
        if _llm_available() is not True:
            reason = "no LLM configured: threshold-only search, keyword homework check"
    mode = "fake" if fake else "real"
    print(f"Rewind API mode: {mode}" + (f" ({reason})" if reason else ""), flush=True)

    def is_homework(question: str) -> bool:
        if not fake and _llm_available() is True:
            return quiz.is_homework(question)
        return _is_homework_keywords(question)

    def reindex(lecture_id: str) -> None:
        if not fake:
            search.index_lecture(lecture_id)

    def default_warmer() -> None:
        # One search loads the embedding model and every lecture index (building missing ones).
        search.search("warm up", top_k=1)

    st = SimpleNamespace(
        log_ask=lambda *a: store.log_ask(*a, db_path=db),
        log_attempt=lambda *a: store.log_attempt(*a, db_path=db),
        flag_gap=lambda *a: store.flag_gap(*a, db_path=db),
        resolve_gap=lambda gap_id: store.resolve_gap(gap_id, db_path=db),
        student_weak_spots=lambda sid: store.student_weak_spots(sid, db_path=db),
        lecturer_stats=lambda lid=None: store.lecturer_stats(lid, db_path=db),
        open_gaps=lambda: store.open_gaps(db_path=db),
        ask_totals=lambda: store.ask_totals(db_path=db),
        seed_demo_data=lambda: store.seed_demo_data(db_path=db),
        log_chat=lambda *a: store.log_chat(*a, db_path=db),
    )

    warm = {"status": "ready" if fake and warmer is None else "warming", "error": None}

    def run_warmup(fn: Callable[[], None]) -> None:
        try:
            fn()
            warm["status"] = "ready"
            print("Rewind API: search model and indexes ready", flush=True)
        except Exception as exc:  # noqa: BLE001 - report it, questions will retry lazily
            warm["status"], warm["error"] = "error", f"{type(exc).__name__}: {exc}"
            print(f"Rewind API: warm-up failed ({warm['error']})", flush=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        fn = warmer or (None if fake else default_warmer)
        if fn is not None:
            warm["status"] = "warming"
            threading.Thread(target=run_warmup, args=(fn,), name="rewind-warmup", daemon=True).start()
        yield

    app = FastAPI(title="Rewind API", lifespan=lifespan)
    app.state.mode = mode
    app.state.warm = warm
    app.state.reindex = reindex
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def lecture_or_404(lecture_id: str) -> dict:
        if not _SAFE_NAME.match(lecture_id):
            raise HTTPException(404, "Unknown lecture.")
        meta = _load_meta(lecture_id)
        if meta is None:
            raise HTTPException(404, "Unknown lecture.")
        return meta

    # ---------------------------------------------------------------- student + lecturer

    @app.get("/api/health")
    def health():
        out = {"ok": True, "mode": mode, "status": warm["status"]}
        if warm["error"]:
            out["error"] = warm["error"]
        return out

    @app.get("/api/lectures")
    def get_lectures():
        return [info for lid in visible_lectures() if (info := lecture_info(lid))]

    @app.get("/api/examples")
    def examples():
        """Up to 3 natural questions, at most one per visible lecture, from real snippet concepts."""
        out = []
        for lid in visible_lectures():
            candidates = []
            for s in lectures.load_snippets(lid):
                q = example_question(s.get("concept", ""), s.get("text", ""))
                if q and q not in out:
                    # Prefer plain concepts ("What are neural networks?") over names and titles.
                    plain = not any(w[:1].isupper() for w in q.split(" ")[1:])
                    candidates.append((not plain, len(candidates), q))
            if candidates:
                out.append(min(candidates)[2])
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
            raise HTTPException(503, scrub_secret(f"Search is unavailable: {exc}", _raw_key())) from exc
        hidden: dict[str, bool] = {}

        def visible(lid: str) -> bool:
            if lid not in hidden:
                hidden[lid] = is_hidden(lid)
            return not hidden[lid]

        def shown(s: dict | None) -> bool:
            return s is not None and visible(s["lecture_id"])

        best = _as_snippet(result.get("best"))
        found = result.get("status") == "found" and shown(best)

        # Slides: the clip's aligned slides first, then slides search matched to the question.
        slide_cache: dict[str, dict[int, dict]] = {}
        slides_out: list[dict] = []

        def add_slide(lid: str, n: Any, score: Any, source: str) -> None:
            if len(slides_out) >= MAX_ASK_SLIDES or not lid or not visible(lid):
                return
            if any(s["lecture_id"] == lid and s["n"] == n for s in slides_out):
                return
            if lid not in slide_cache:
                slide_cache[lid] = {s.get("n"): s for s in load_slides(lid)}
            slide = slide_cache[lid].get(n)
            if slide is None:
                return
            pub = public_slide(lid, slide)
            if pub["image_url"]:
                pub.pop("text", None)  # text-only decks still need readable slide content
            pub.update({"score": score if isinstance(score, (int, float)) else None, "source": source})
            slides_out.append(pub)

        if found:
            for n in best.get("slides") or []:
                add_slide(best["lecture_id"], n, None, "clip")
        for s in result.get("slides") or []:
            if isinstance(s, dict):
                add_slide(s.get("lecture_id"), s.get("n"), s.get("score"), "search")

        slide_only = not found and result.get("status") == "slide_only" and bool(slides_out)
        # A hidden best match isn't replaced by an alternative: alternatives didn't pass the check.
        if found:
            logged = result
        else:
            status = "slide_only" if slide_only else "not_covered"
            logged = {"status": status, "best": None, "score": result.get("score")}
        st.log_ask(body.student_id, question, logged)
        homework = is_homework(question)
        if slide_only:
            # Covered by the slides, so not a gap for the lecturer.
            return {"status": "slide_only", "homework": homework, "snippet": None,
                    "lecture": lecture_info(slides_out[0]["lecture_id"]), "alternatives": [],
                    "slides": slides_out}
        if not found:
            st.flag_gap(body.student_id, question)
            return {"status": "not_covered", "homework": homework, "snippet": None,
                    "lecture": None, "alternatives": [], "slides": []}
        cache: dict = {}
        main = _with_lecture(best, cache)
        alts = [_with_lecture(a, cache) for a in map(_as_snippet, result.get("alternatives", [])) if shown(a)]
        return {"status": "found", "homework": homework, "snippet": main["snippet"],
                "lecture": main["lecture"], "alternatives": alts, "slides": slides_out}

    # ---------------------------------------------------------------- follow-up chat

    @app.post("/api/chat")
    def chat(body: ChatBody, request: Request):
        ok, why = chat_status()
        if not ok:
            raise HTTPException(409, why)
        snippet = lectures.get_snippet(body.snippet_id)
        if snippet is None:
            raise HTTPException(404, "Unknown snippet.")
        messages = [m.model_dump() for m in body.messages][-MAX_CHAT_MESSAGES:]
        if not body.student_id.strip() or any(not m["content"].strip() for m in messages):
            raise HTTPException(400, "Student ID and messages must not be blank.")
        if messages[-1]["role"] != "user" or not messages[-1]["content"].strip():
            raise HTTPException(400, "The last message must be the student's question.")
        respond = importlib.import_module("rewind.chat").respond
        student_id, snippet_id = body.student_id, body.snippet_id
        st.log_chat(student_id, snippet_id, "user", messages[-1]["content"].strip())

        async def events():
            parts: list[str] = []
            source = None
            end = object()
            try:
                source = iter(respond(student_id, snippet_id, messages))
                while not await request.is_disconnected():
                    event = await run_in_threadpool(next, source, end)
                    if await request.is_disconnected():
                        return
                    if event is end:
                        raise RuntimeError("The AI stream ended before completion.")
                    if not isinstance(event, dict):
                        raise ValueError("Invalid chat event.")
                    event = scrub_secret(event, _raw_key())
                    if event.get("type") == "text":
                        if not isinstance(event.get("text"), str):
                            raise ValueError("Invalid chat text event.")
                        parts.append(event["text"])
                    elif event.get("type") == "done":
                        if (not isinstance(event.get("citations"), list)
                            or type(event.get("outside_materials")) is not bool
                            or type(event.get("homework")) is not bool
                            or not "".join(parts).strip()):
                            raise ValueError("Invalid chat completion event.")
                        # Only a completed turn belongs in assistant history. Persist before
                        # telling the browser it succeeded; failures/interruptions stay partial.
                        st.log_chat(student_id, snippet_id, "assistant", "".join(parts),
                                    event["outside_materials"])
                        yield sse(event)
                        return
                    elif event.get("type") == "error":
                        yield sse(event)
                        return
                    else:
                        raise ValueError("Unknown chat event type.")
                    yield sse(event)
            except Exception as exc:  # noqa: BLE001 - the stream has started; report as an event
                message = scrub_secret(f"{type(exc).__name__}: {exc}", _raw_key())
                yield sse({"type": "error", "message": f"The AI couldn't answer right now ({message})."})
            finally:
                close = getattr(source, "close", None)
                if callable(close):
                    # Starlette cancels the response task on disconnect. Shield cleanup so
                    # the underlying provider generator can release its stream as well.
                    import anyio

                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(close)

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
                "followups": r.get("followups", 0),
            })
        return {
            "totals": {
                "asks": n,
                "found_pct": round(100 * totals["found"] / n, 1) if n else 0.0,
                "not_covered_pct": round(100 * totals["not_covered"] / n, 1) if n else 0.0,
                "slide_only_pct": round(100 * totals.get("slide_only", 0) / n, 1) if n else 0.0,
                "followups": sum(r.get("followups", 0) for r in rows),
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

        for lid in visible_lectures():
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

    # ---------------------------------------------------------------- AI settings (local only)

    @app.get("/api/ai-status")
    def ai_status():
        """Public: only what the student page needs. Never includes model, key or base URL."""
        llm_ok = _llm_available() is True
        pub: dict = {}
        try:
            pub = _optional("settings", "public")() or {}
        except Exception:  # noqa: BLE001 - HTTPException(501) included: fall back to config
            pub = {}
        provider, model = pub.get("provider"), pub.get("model")
        if provider is None:
            try:
                provider = config.get_provider()
            except Exception:  # noqa: BLE001
                provider = None
        if model is None:
            try:
                model = config.get_model() or None
            except Exception:  # noqa: BLE001
                model = None
        result = {
            "mode": pub.get("mode") or ("api" if llm_ok else "none"),
            "provider": provider,
            "preset": pub["preset"] if "preset" in pub else preset_for(provider, pub.get("base_url")),
            "model": model,
            "can_generate": llm_ok,
            "chat_available": chat_status()[0],
            "coverage_check": llm_ok and not fake,
        }
        return scrub_secret(result, _raw_key())

    @app.get("/api/settings", dependencies=[Depends(local_only)])
    def get_settings():
        return _public_settings()

    @app.post("/api/settings", dependencies=[Depends(local_only)])
    def post_settings(body: SettingsBody):
        current = dict(_optional("settings", "load")())
        save = _optional("settings", "save")
        updates = body.model_dump(exclude_none=True)
        if not (updates.get("api_key") or "").strip():
            updates.pop("api_key", None)  # omitted or empty: keep the current key
        current.update(updates)
        try:
            save(current)
        except ValueError as exc:
            raise HTTPException(422, scrub_secret(str(exc), _raw_key())) from None
        return _public_settings()

    @app.post("/api/settings/test", dependencies=[Depends(local_only)])
    def test_settings():
        result = _optional("llm", "test_connection")()
        return scrub_secret(result, _raw_key())

    # ---------------------------------------------------------------- context package (local only)

    @app.get("/api/lectures/{lecture_id}/package", dependencies=[Depends(local_only)])
    def package(lecture_id: str):
        lecture_or_404(lecture_id)
        data = _optional("context_package", "build_package")(lecture_id)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="rewind-{lecture_id}-package.zip"'},
        )

    @app.get("/api/lectures/{lecture_id}/prompt", dependencies=[Depends(local_only)])
    def prompt(lecture_id: str):
        lecture_or_404(lecture_id)
        return {"parts": list(_optional("context_package", "build_chat_prompt")(lecture_id))}

    @app.post("/api/lectures/{lecture_id}/import", dependencies=[Depends(local_only)])
    def import_answer(lecture_id: str, body: ImportBody, request: Request):
        lecture_or_404(lecture_id)
        result = dict(_optional("context_package", "import_answer")(lecture_id, body.raw_text))
        if result.get("ok"):
            aligned = realign(lecture_id)
            if aligned is not None:
                result["slides_aligned"] = aligned
            try:
                request.app.state.reindex(lecture_id)
                result["reindexed"] = True
            except Exception as exc:  # noqa: BLE001 - snippets are saved; search reindexes lazily
                result["reindexed"] = False
                result["reindex_error"] = f"{type(exc).__name__}: {exc}"
        return result

    # ---------------------------------------------------------------- generate (local only)

    gen_state: dict[str, dict] = {}
    gen_lock = threading.Lock()

    def set_state(lecture_id: str, **values) -> None:
        with gen_lock:
            gen_state[lecture_id] = values

    def generate_job(lecture_id: str, title: str | None, reindex_fn: Callable[[str], None]) -> None:
        try:
            from rewind import snippet_maker

            transcript = lectures.load_transcript(lecture_id)
            # One-time preprocessing uses the stronger preprocess model (purpose="preprocess").
            snippets = snippet_maker.make_snippets(transcript, lecture_id, title=title, **ingest.preprocess_kwargs())
            if not snippets:
                raise ValueError("The AI's answer contained no usable snippets. Try again or use the context package.")
            lectures.save_snippets(lecture_id, snippets)
            realign(lecture_id)  # new snippets have no "slides" yet
            reindex_fn(lecture_id)
            set_state(lecture_id, state="done", message=f"Made {len(snippets)} snippets.",
                      snippets_count=len(snippets))
        except Exception as exc:  # noqa: BLE001 - report to the settings page
            msg = scrub_secret(f"{type(exc).__name__}: {exc}", _raw_key())
            set_state(lecture_id, state="error", message=msg, snippets_count=None)

    @app.post("/api/lectures/{lecture_id}/generate", dependencies=[Depends(local_only)])
    def generate(lecture_id: str, request: Request):
        meta = lecture_or_404(lecture_id)
        if not (config.LECTURES_DIR / lecture_id / "transcript.json").exists():
            return {"ok": False, "snippets_count": None, "error": "This lecture has no transcript.json yet."}
        if _llm_available() is not True:
            return {"ok": False, "snippets_count": None,
                    "error": "No AI API is set up. Choose \"Use an API\" in AI settings, or use the context package."}
        with gen_lock:
            if gen_state.get(lecture_id, {}).get("state") == "running":
                return {"ok": False, "snippets_count": None, "error": "Already generating snippets for this lecture."}
            gen_state[lecture_id] = {"state": "running", "message": "Asking the AI for snippets…",
                                     "snippets_count": None}
        threading.Thread(target=generate_job, args=(lecture_id, meta.get("title"), request.app.state.reindex),
                         name=f"rewind-generate-{lecture_id}", daemon=True).start()
        return {"ok": True, "snippets_count": None, "error": None, "state": "running"}

    @app.get("/api/lectures/{lecture_id}/generate/estimate", dependencies=[Depends(local_only)])
    def generate_estimate(lecture_id: str):
        """Cost/size estimate for Generate, without calling the AI.

        Builds initial generation requests (one per transcript window; excludes verification/repair) and
        passes them to llm.estimate(requests, purpose="preprocess"), each request
        {"system", "user", "max_tokens"}, so it's priced with the preprocess model Generate uses.
        """
        meta = lecture_or_404(lecture_id)
        estimate = _optional("llm", "estimate")
        if not (config.LECTURES_DIR / lecture_id / "transcript.json").exists():
            raise HTTPException(409, "This lecture has no transcript.json yet.")
        requests = snippet_requests(lecture_id, meta.get("title"))
        for item in requests:
            item["purpose"] = "preprocess"
        result = estimate(requests, **_with_purpose(estimate, "preprocess"))
        if isinstance(result, dict):
            result["scope"] = "Initial snippet generation; verification, repair and retries are excluded."
        return scrub_secret(result, _raw_key())

    @app.get("/api/lectures/{lecture_id}/generate/status", dependencies=[Depends(local_only)])
    def generate_status(lecture_id: str):
        lecture_or_404(lecture_id)
        with gen_lock:
            return dict(gen_state.get(lecture_id, {"state": "idle", "message": "", "snippets_count": None}))

    # ---------------------------------------------------------------- slides

    def realign(lecture_id: str) -> bool | None:
        """Re-run slides.align after snippets change. None if the lecture has no slides."""
        if not _has_slides(lecture_id):
            return None
        try:
            _optional("slides", "align")(lecture_id)
            return True
        except Exception:  # noqa: BLE001 - HTTPException(501) included; snippets still work without slides
            return False

    @app.post("/api/lectures/{lecture_id}/slides", dependencies=[Depends(local_only)])
    async def upload_slides(lecture_id: str, request: Request):
        """Multipart field "file": a PDF or PPTX. Saved to source/, then slides.ingest_slides + align.

        The form is parsed only after the local-only check, so remote clients can't upload at all.
        """
        lecture_or_404(lecture_id)
        ingest_fn = _optional("slides", "ingest_slides")
        # Bound the incoming body before the multipart parser spools it to disk.
        # Leave room for headers and the few small metadata fields.
        received = 0

        async def receive():
            nonlocal received
            message = await request.receive()
            received += len(message.get("body", b""))
            if received > MAX_SLIDES_BYTES + 64 * 1024:
                raise HTTPException(413, "Slide upload exceeds the size limit.")
            return message

        bounded = Request(request.scope, receive=receive)
        try:
            async with bounded.form(max_files=1, max_fields=5, max_part_size=16 * 1024) as form:
                upload = form.get("file")
                if not isinstance(upload, UploadFile) or not upload.filename:
                    raise HTTPException(400, 'Send the slides as a multipart file field named "file".')
                try:
                    name = ingest.safe_filename(upload.filename, ingest.SLIDE_EXTENSIONS)
                except ingest.IngestError as exc:
                    raise HTTPException(400, str(exc)) from None
                data = bytearray()
                while chunk := await upload.read(1024 * 1024):
                    data += chunk
                    if len(data) > MAX_SLIDES_BYTES:
                        raise HTTPException(413, "Slide upload exceeds the size limit.")
                if not data:
                    raise HTTPException(400, "The uploaded file is empty.")
                path = await run_in_threadpool(ingest.save_source, lecture_id, name, bytes(data))
        except (ImportError, AssertionError):
            raise HTTPException(501, "Slide upload requires python-multipart.") from None
        except ingest.IngestError as exc:
            raise HTTPException(400, str(exc)) from None
        try:
            count = await run_in_threadpool(ingest_fn, lecture_id, path)
        except Exception as exc:  # noqa: BLE001 - unreadable or unsupported file
            raise HTTPException(422, scrub_secret(f"Couldn't read the slides: {type(exc).__name__}: {exc}", _raw_key())) from exc
        out: dict = {"count": int(count), "aligned": False}
        if lectures.load_snippets(lecture_id):
            try:
                await run_in_threadpool(_optional("slides", "align"), lecture_id)
                out["aligned"] = True
            except Exception as exc:  # noqa: BLE001
                out["align_error"] = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}: {exc}"
        try:
            await run_in_threadpool(request.app.state.reindex, lecture_id)  # search indexes slides too
        except Exception as exc:  # noqa: BLE001 - slides saved; search reindexes lazily
            out["reindex_error"] = f"{type(exc).__name__}: {exc}"
        return scrub_secret(out, _raw_key())

    @app.get("/api/lectures/{lecture_id}/slides")
    def get_slides(lecture_id: str):
        """Public: slides.json plus an image_url per slide. [] when the lecture has no slides."""
        lecture_or_404(lecture_id)
        if is_hidden(lecture_id):
            raise HTTPException(404, "Unknown lecture.")
        return [{**s, "image_url": slide_image_url(lecture_id, s)} for s in load_slides(lecture_id)]

    # ---------------------------------------------------------------- media + static

    @app.get("/media/{lecture_id}/slides/{filename}")
    def slide_media(lecture_id: str, filename: str):
        if not _SAFE_NAME.match(lecture_id) or not _SAFE_NAME.match(filename):
            raise HTTPException(404, "Not found.")
        if Path(filename).suffix.lower() not in SLIDE_IMAGE_EXTENSIONS:
            raise HTTPException(404, "Not found.")
        root = config.LECTURES_DIR.resolve()
        folder = (root / lecture_id).resolve()
        base = (folder / "slides").resolve()
        path = (base / filename).resolve()
        if (not folder.is_relative_to(root) or not base.is_relative_to(folder)
            or not path.is_relative_to(base) or not path.is_file()):
            raise HTTPException(404, "Not found.")
        return FileResponse(path)

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
        app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
