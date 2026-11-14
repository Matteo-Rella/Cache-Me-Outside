"""Turn a lecture transcript into topic snippets (snippets.json contract in AGENTS.md).

Inputs:  a transcript (list of {"start", "end", "text"}), a lecture id, optionally a title.
Outputs: make_snippets() returns a list of snippet dicts; the CLI also saves
         data/lectures/<lecture_id>/{meta,transcript,snippets}.json and prints a table.

CLI:
  python -m rewind.snippet_maker --youtube <URL> --lecture-id econ01 [--title "..."] [--course "..."] [--lecture-number 1]
  python -m rewind.snippet_maker --transcript path/to/transcript.json --video-url <URL> --lecture-id econ01
  Without an API key:
  ... --export-prompt prompt.txt   save transcript/meta, write the exact prompt(s), no API call
  python -m rewind.snippet_maker --lecture-id econ01 --import-raw raw.json
                                   post-process raw model JSON against the saved transcript

Pipeline: format the transcript as "[123.4] text" lines -> one LLM call per window
(one window for anything under ~100k characters) -> fix the output in code:
snap times to segment boundaries, sort, remove overlaps, drop short snippets, warn on
long ones, validate questions, fill "text", assign ids "<lecture_id>-NNN".
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from rewind import lectures, transcript_io
from rewind.llm import ask_json

log = logging.getLogger(__name__)

MIN_SECONDS = 20  # shorter snippets are dropped
MAX_SECONDS = 150  # longer snippets are kept but flagged
WINDOW_CHARS = 100_000  # formatted transcript size that fits comfortably in one call
OVERLAP_CHARS = 8_000  # overlap between windows for long lectures
MAX_OUTPUT_TOKENS = 32_000

SNIPPET_PROMPT = """\
You cut a recorded university lecture into short, self-contained topic snippets. \
Students will search these snippets and jump straight to the moment in the video, \
so each one must make sense on its own.

The transcript comes as lines "[seconds] text"; the number is when that line starts.

Return JSON only: an array, no prose, no code fences. Each element:
{"concept": string, "start": number, "end": number, "summary": string,
 "questions": [{"q": string, "options": [string, string, string, string], "answer": integer, "explanation": string}]}

HOW TO CUT
- Start a new snippet where the professor moves to a new idea, definition, example or argument.
- start = the timestamp of the line where the topic is introduced.
  end = the timestamp of the first line AFTER the snippet. Copy timestamps from the transcript.
- Caption lines often split sentences. Start on a line where the first sentence of the new
  topic begins, even if that line also holds the last words of the previous sentence; never
  start on a line that continues a sentence begun on the line before. End after the line
  that completes the topic's last sentence. A few extra words at the edges are better than a
  cut-off sentence.
- Each snippet lasts 45-120 seconds, never more than 150. Split a long explanation into
  consecutive parts (for example "X: definition", then "X: worked example"). Merge a topic
  shorter than 45 seconds into the neighbouring snippet it belongs to.
- Snippets must not overlap. Gaps are fine.
- Leave out admin talk: attendance, breaks, exam dates and logistics, room or tech problems,
  small talk, the professor's teaching style, and agendas or roadmaps that only list topics
  for this or later lectures without explaining them. Only if the transcript contains nothing
  but admin talk, cut that.
- Otherwise cover all the teaching content in order; don't skip topics.

concept: a short noun phrase a student would type into a search box, e.g. "Price elasticity
of demand", not "The professor discusses elasticity". Specific, and unique within the lecture.

summary: exactly 2 sentences. Only facts, definitions and examples the professor says in this
snippet. No outside knowledge, no added facts, no "the professor explains" framing.

questions: exactly 2 multiple-choice questions per snippet, 4 options each, one correct.
- Test understanding: apply the idea to a new small case, predict an outcome, or choose the
  correct explanation. Never ask to recall a word, name or number verbatim.
- Answerable from this snippet alone.
- Wrong options are plausible misconceptions, similar in length to the right one.
  No "all of the above" or "none of the above".
- answer is the 0-based index of the correct option. Spread the correct option evenly over
  positions 0-3 across the whole lecture; it must not be in the same position most of the time.
- explanation: 1-2 sentences on why the right option is right, grounded in what the professor
  said in this snippet.

LANGUAGE: write concept, summary, questions, options and explanations in the language the
professor speaks (Italian lecture -> Italian, English lecture -> English). Keep technical terms
as the professor uses them.
"""

WINDOW_NOTE = """\
This is part {part} of {total} of a longer lecture, covering {start} to {end} seconds. \
Neighbouring parts overlap with this one. Only return snippets whose topic starts inside \
this part; if a topic is cut off at the end of the part, end the snippet at the last line shown. \
Use the absolute timestamps shown.
"""


# ---------------------------------------------------------------- formatting


def format_transcript(segments: list[dict]) -> str:
    """Segments -> '[123.4] text' lines for the LLM."""
    return "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in segments)


def split_windows(
    segments: list[dict], max_chars: int | None = None, overlap_chars: int | None = None
) -> list[list[dict]]:
    """Split segments into windows of at most ~max_chars formatted characters
    (default WINDOW_CHARS). Consecutive windows share about overlap_chars (default OVERLAP_CHARS)."""
    max_chars = WINDOW_CHARS if max_chars is None else max_chars
    overlap_chars = OVERLAP_CHARS if overlap_chars is None else overlap_chars
    sizes = [len(f"[{s['start']:.1f}] {s['text']}") + 1 for s in segments]
    if sum(sizes) <= max_chars:
        return [segments]
    windows: list[list[dict]] = []
    i = 0
    n = len(segments)
    while i < n:
        j, total = i, 0
        while j < n and (total + sizes[j] <= max_chars or j == i):
            total += sizes[j]
            j += 1
        windows.append(segments[i:j])
        if j >= n:
            break
        # step back from j until we have ~overlap_chars of overlap
        k, back = j, 0
        while k - 1 > i and back + sizes[k - 1] <= overlap_chars:
            k -= 1
            back += sizes[k]
        i = k
    return windows


def _fmt_time(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------- LLM call


def _as_list(data: Any) -> list[dict]:
    if isinstance(data, dict):
        for key in ("snippets", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    raise ValueError(f"Expected a JSON array of snippets, got {type(data).__name__}")


def build_user_messages(segments: list[dict], title: str | None = None) -> list[str]:
    """The user message for each LLM window, exactly as sent (system prompt is SNIPPET_PROMPT)."""
    windows = split_windows(segments)
    messages = []
    for idx, window in enumerate(windows, 1):
        parts = []
        if title:
            parts.append(f"Lecture title: {title}")
        if len(windows) > 1:
            parts.append(
                WINDOW_NOTE.format(
                    part=idx, total=len(windows), start=window[0]["start"], end=window[-1]["end"]
                )
            )
        parts.append("Transcript:\n" + format_transcript(window))
        messages.append("\n\n".join(parts))
    return messages


def request_raw_snippets(
    segments: list[dict], title: str | None = None, ask: Callable[..., Any] | None = None
) -> list[dict]:
    """Call the LLM once per window and return the raw (unvalidated) snippet dicts."""
    ask = ask or ask_json
    messages = build_user_messages(segments, title)
    raw: list[dict] = []
    for idx, user in enumerate(messages, 1):
        log.info("LLM call %d/%d", idx, len(messages))
        raw.extend(_as_list(ask(SNIPPET_PROMPT, user, max_tokens=MAX_OUTPUT_TOKENS)))
    return raw


def export_prompt(segments: list[dict], path: str | Path, title: str | None = None) -> list[Path]:
    """Write system prompt + user message to `path` (path.partN.ext per window if split). No API call."""
    path = Path(path)
    messages = build_user_messages(segments, title)
    written = []
    for idx, user in enumerate(messages, 1):
        target = path if len(messages) == 1 else path.with_name(f"{path.stem}.part{idx}{path.suffix}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"===== SYSTEM =====\n{SNIPPET_PROMPT}\n===== USER =====\n{user}\n", encoding="utf-8"
        )
        written.append(target)
    return written


def import_raw(path: str | Path, segments: list[dict], lecture_id: str) -> list[dict]:
    """Read raw model output (one JSON array, as ask_json would return) and post-process it."""
    from rewind.llm import parse_json

    raw = _as_list(parse_json(Path(path).read_text(encoding="utf-8")))
    return finalize(raw, transcript_io.validate_segments(segments), lecture_id)


# ---------------------------------------------------------------- fixing the output


def snap(t: float, boundaries: list[float]) -> float:
    """Return the value in sorted `boundaries` nearest to t (earlier one on ties)."""
    i = bisect.bisect_left(boundaries, t)
    if i == 0:
        return boundaries[0]
    if i == len(boundaries):
        return boundaries[-1]
    before, after = boundaries[i - 1], boundaries[i]
    return before if t - before <= after - t else after


def snap_snippet(snippet: dict, segments: list[dict]) -> dict:
    """Snap start to the nearest segment start and end to the nearest segment end."""
    starts = [s["start"] for s in segments]
    ends = sorted(s["end"] for s in segments)
    out = dict(snippet)
    out["start"] = snap(float(snippet["start"]), starts)
    out["end"] = snap(float(snippet["end"]), ends)
    return out


def remove_overlaps(snippets: list[dict], segments: list[dict], min_seconds: float | None = None) -> list[dict]:
    """Sort by start and resolve overlaps.

    If a snippet overlaps the previous one by half its length or more, it is a duplicate
    (common where LLM windows overlap) and is dropped. A smaller overlap is trimmed by moving
    its start to the first segment that begins at or after the previous snippet's end.
    Snippets shorter than min_seconds (default MIN_SECONDS, before or after trimming) are dropped.
    """
    min_seconds = MIN_SECONDS if min_seconds is None else min_seconds
    starts = [s["start"] for s in segments]
    kept: list[dict] = []
    for s in sorted(snippets, key=lambda x: (x["start"], -x["end"])):
        s = dict(s)
        if kept and s["start"] < kept[-1]["end"]:
            prev = kept[-1]
            overlap = min(prev["end"], s["end"]) - s["start"]
            if overlap >= 0.5 * (s["end"] - s["start"]):
                log.warning("Dropping %r: duplicates %r", s.get("concept"), prev.get("concept"))
                continue
            i = bisect.bisect_left(starts, prev["end"] - 1e-6)
            s["start"] = starts[i] if i < len(starts) else prev["end"]
        if s["end"] - s["start"] < min_seconds:
            log.warning("Dropping %r: only %.0f s", s.get("concept"), s["end"] - s["start"])
            continue
        kept.append(s)
    return kept


def _answer_index(answer: Any, options: list[str]) -> int | None:
    if isinstance(answer, bool):
        return None
    if isinstance(answer, int):
        return answer if 0 <= answer < len(options) else None
    if isinstance(answer, float) and answer.is_integer():
        return _answer_index(int(answer), options)
    if isinstance(answer, str):
        a = answer.strip()
        if a.isdigit():
            return _answer_index(int(a), options)
        if len(a) == 1 and a.upper() in "ABCD":
            return "ABCD".index(a.upper())
        if a in options:
            return options.index(a)
    return None


def validate_question(q: Any) -> dict | None:
    """Return a clean question dict, or None if it can't be fixed."""
    if not isinstance(q, dict):
        return None
    text = str(q.get("q") or q.get("question") or "").strip()
    options = q.get("options")
    explanation = str(q.get("explanation") or "").strip()
    if not text or not explanation or not isinstance(options, list) or len(options) != 4:
        return None
    options = [str(o).strip() for o in options]
    if not all(options) or len(set(options)) != 4:
        return None
    answer = _answer_index(q.get("answer"), options)
    if answer is None:
        return None
    return {"q": text, "options": options, "answer": answer, "explanation": explanation}


def validate_snippet(raw: Any) -> dict | None:
    """Check required fields and types; clean the questions. None if unusable."""
    if not isinstance(raw, dict):
        return None
    try:
        start, end = float(raw["start"]), float(raw["end"])
    except (KeyError, TypeError, ValueError):
        log.warning("Dropping snippet with bad times: %r", raw.get("concept"))
        return None
    concept = str(raw.get("concept") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    if not concept or not summary or end <= start:
        log.warning("Dropping invalid snippet: %r", concept or raw)
        return None
    questions = [v for q in (raw.get("questions") or []) if (v := validate_question(q))]
    if len(questions) < len(raw.get("questions") or []) or len(questions) < 2:
        log.warning("%r: %d valid question(s)", concept, len(questions))
    return {"concept": concept, "start": start, "end": end, "summary": summary, "questions": questions}


def text_between(segments: list[dict], start: float, end: float) -> str:
    """Join the text of the segments that lie inside [start, end]."""
    eps = 1e-6
    return " ".join(s["text"] for s in segments if s["start"] >= start - eps and s["end"] <= end + eps)


def warn_answer_skew(questions: list[dict], max_share: float = 0.5, min_questions: int = 8) -> bool:
    """Warn (and return True) if one answer position holds more than max_share of the answers.

    Models tend to put the right option in the same slot, which lets students game the quiz.
    """
    if len(questions) < min_questions:
        return False
    counts = [0, 0, 0, 0]
    for q in questions:
        counts[q["answer"]] += 1
    top = max(counts)
    if top / len(questions) > max_share:
        log.warning("Correct answer is option %s in %d of %d questions; regenerate or reshuffle options",
                    "ABCD"[counts.index(top)], top, len(questions))
        return True
    return False


def finalize(raw_snippets: list[Any], segments: list[dict], lecture_id: str) -> list[dict]:
    """Validate, snap, de-overlap, fill text and assign ids. Pure function (no LLM)."""
    valid = [v for r in raw_snippets if (v := validate_snippet(r))]
    snapped = [snap_snippet(s, segments) for s in valid]
    snapped = [s for s in snapped if s["end"] > s["start"]]
    cleaned = remove_overlaps(snapped, segments)
    warn_answer_skew([q for s in cleaned for q in s["questions"]])
    out = []
    for i, s in enumerate(cleaned, 1):
        duration = s["end"] - s["start"]
        if duration > MAX_SECONDS:
            log.warning("%r is %.0f s long (over %d s)", s["concept"], duration, MAX_SECONDS)
        out.append(
            {
                "id": f"{lecture_id}-{i:03d}",
                "lecture_id": lecture_id,
                "concept": s["concept"],
                "start": round(s["start"], 2),
                "end": round(s["end"], 2),
                "summary": s["summary"],
                "text": text_between(segments, s["start"], s["end"]),
                "questions": s["questions"],
            }
        )
    return out


def make_snippets(
    transcript: list[dict], lecture_id: str, title: str | None = None, ask: Callable[..., Any] | None = None
) -> list[dict]:
    """Transcript segments -> list of snippets following the snippets.json contract.

    `ask` defaults to rewind.llm.ask_json(system, user, max_tokens=...); pass a fake in tests.
    """
    segments = transcript_io.validate_segments(transcript)
    if not segments:
        raise ValueError("Transcript is empty")
    raw = request_raw_snippets(segments, title=title, ask=ask)
    return finalize(raw, segments, lecture_id)


# ---------------------------------------------------------------- CLI


def print_table(snippets: list[dict], out=None) -> None:
    out = out or sys.stdout
    width = max([len("concept")] + [min(len(s["concept"]), 48) for s in snippets])
    print(f"{'id':<14} {'concept':<{width}}  {'time':<13}  dur", file=out)
    print("-" * (14 + width + 22), file=out)
    for s in snippets:
        dur = s["end"] - s["start"]
        concept = s["concept"] if len(s["concept"]) <= 48 else s["concept"][:45] + "..."
        span = f"{_fmt_time(s['start'])}-{_fmt_time(s['end'])}"
        flag = "  !" if dur > MAX_SECONDS else ""
        print(f"{s['id']:<14} {concept:<{width}}  {span:<13}  {dur:>4.0f}s{flag}", file=out)
    total = sum(s["end"] - s["start"] for s in snippets)
    print(f"\n{len(snippets)} snippets, {_fmt_time(total)} of lecture covered.", file=out)


def youtube_title(url: str) -> str | None:
    """Video title via YouTube's public oEmbed endpoint; None if unavailable."""
    try:
        q = urllib.parse.urlencode({"url": url, "format": "json"})
        with urllib.request.urlopen(f"https://www.youtube.com/oembed?{q}", timeout=10) as r:
            return json.load(r).get("title")
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m rewind.snippet_maker", description=__doc__.split("\n")[0])
    src = p.add_mutually_exclusive_group()
    src.add_argument("--youtube", metavar="URL", help="fetch captions from this YouTube video")
    src.add_argument("--transcript", metavar="PATH", help="transcript.json following the contract")
    p.add_argument("--video-url", help="video URL for meta.json (required with --transcript)")
    p.add_argument("--lecture-id", required=True)
    p.add_argument("--title")
    p.add_argument("--course")
    p.add_argument("--lecture-number", type=int)
    p.add_argument("--languages", default="it,en", help="caption languages in order of preference")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--export-prompt", metavar="FILE",
                      help="write the system prompt + user message(s) to FILE instead of calling the API")
    mode.add_argument("--import-raw", metavar="FILE",
                      help="post-process raw model JSON from FILE instead of calling the API "
                           "(without --youtube/--transcript, uses the lecture's saved transcript.json)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.transcript and not args.video_url:
        p.error("--video-url is required with --transcript")
    if not (args.youtube or args.transcript or args.import_raw):
        p.error("one of --youtube or --transcript is required")

    lecture_id = args.lecture_id
    if args.import_raw and not (args.youtube or args.transcript):
        segments = lectures.load_transcript(lecture_id)
        snippets = import_raw(args.import_raw, segments, lecture_id)
        lectures.save_snippets(lecture_id, snippets)
        print_table(snippets)
        print(f"Saved {lectures.lecture_dir(lecture_id) / 'snippets.json'}")
        return 0

    if args.youtube:
        video_id = transcript_io.extract_video_id(args.youtube)
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        segments, lang = transcript_io.from_youtube(video_id, tuple(args.languages.split(",")))
        log.info("Fetched %d caption segments (%s)", len(segments), lang)
        title = args.title or youtube_title(video_url)
    else:
        video_url = args.video_url
        segments = transcript_io.load_transcript(args.transcript)
        title = args.title

    meta_path = lectures.lecture_dir(lecture_id) / "meta.json"
    meta = lectures.load_meta(lecture_id) if meta_path.exists() else {}
    meta.update(
        {
            "lecture_id": lecture_id,
            "title": title or meta.get("title") or lecture_id,
            "course": args.course or meta.get("course", ""),
            "lecture_number": args.lecture_number or meta.get("lecture_number"),
            "video_url": video_url,
        }
    )
    lectures.save_meta(lecture_id, meta)
    lectures.save_transcript(lecture_id, segments)

    if args.export_prompt:
        for path in export_prompt(transcript_io.validate_segments(segments), args.export_prompt, meta["title"]):
            print(f"Wrote {path}")
        return 0
    if args.import_raw:
        snippets = import_raw(args.import_raw, segments, lecture_id)
    else:
        snippets = make_snippets(segments, lecture_id, title=meta["title"])
    lectures.save_snippets(lecture_id, snippets)
    print_table(snippets)
    print(f"Saved {lectures.lecture_dir(lecture_id) / 'snippets.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
