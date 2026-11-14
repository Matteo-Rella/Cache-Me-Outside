"""Load, validate and convert lecture transcripts to the transcript.json contract.

Contract: list of {"start": float, "end": float, "text": str}, times in seconds,
sorted by start, start < end, non-empty text.

- load_transcript(path) -> validated segments
- from_whisper(result) -> segments, from openai-whisper or faster-whisper output
- from_youtube(video_id_or_url, languages) -> (segments, language_code)
- merge_short_segments(segments, min_seconds) -> segments
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_NOISE_RE = re.compile(r"^\[(music|applause|laughter|musica|applausi|risate)\]$", re.I)


def validate_segments(segments: list[dict]) -> list[dict]:
    """Return clean segments or raise ValueError describing the first problem."""
    out: list[dict] = []
    prev_start = float("-inf")
    for i, seg in enumerate(segments):
        try:
            start, end = float(seg["start"]), float(seg["end"])
            text = str(seg["text"]).strip()
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"segment {i}: missing or bad field ({err})") from err
        if not text:
            raise ValueError(f"segment {i}: empty text")
        if not start < end:
            raise ValueError(f"segment {i}: start {start} is not before end {end}")
        if start < prev_start:
            raise ValueError(f"segment {i}: not sorted by start")
        prev_start = start
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return out


def load_transcript(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return validate_segments(json.load(f))


def _clean_text(text: str) -> str:
    return " ".join(str(text).replace("\n", " ").split())


def _from_raw(items: list[tuple[float, float, str]]) -> list[dict]:
    """(start, end, text) -> contract segments. Drops empty/noise lines, sorts,
    and clips each end to the next start so segments never overlap."""
    rows = []
    for start, end, text in items:
        text = _clean_text(text)
        if not text or _NOISE_RE.match(text):
            continue
        rows.append([float(start), float(end), text])
    rows.sort(key=lambda r: r[0])
    out: list[dict] = []
    for i, (start, end, text) in enumerate(rows):
        if i + 1 < len(rows):
            end = min(end, rows[i + 1][0]) if rows[i + 1][0] > start else end
        if out and start <= out[-1]["start"]:  # same start as previous: merge
            out[-1]["text"] += " " + text
            out[-1]["end"] = max(out[-1]["end"], round(end, 2))
            continue
        if end <= start:
            end = start + 0.5
        out.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return validate_segments(out)


def from_whisper(result) -> list[dict]:
    """Accept openai-whisper's dict ({"segments": [...]}) or faster-whisper's
    iterable of Segment objects (attributes start/end/text)."""
    segs = result["segments"] if isinstance(result, dict) else result
    items = []
    for s in segs:
        if isinstance(s, dict):
            items.append((s["start"], s["end"], s["text"]))
        else:
            items.append((s.start, s.end, s.text))
    return _from_raw(items)


def extract_video_id(url_or_id: str) -> str:
    """YouTube URL (watch, youtu.be, embed, shorts, live) or bare id -> 11-char id."""
    s = url_or_id.strip()
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    u = urlparse(s if "//" in s else "https://" + s)
    host = (u.hostname or "").removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        candidate = u.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        qs = parse_qs(u.query)
        if "v" in qs:
            candidate = qs["v"][0]
        else:
            parts = [p for p in u.path.split("/") if p]
            candidate = parts[1] if len(parts) >= 2 and parts[0] in ("embed", "shorts", "live", "v") else ""
    else:
        candidate = ""
    if not re.fullmatch(r"[\w-]{11}", candidate):
        raise ValueError(f"Could not find a YouTube video id in {url_or_id!r}")
    return candidate


def from_youtube(video_id: str, languages: tuple[str, ...] = ("it", "en")) -> tuple[list[dict], str]:
    """Fetch captions with youtube-transcript-api. Prefers manual captions over
    auto-generated ones, in the order of `languages`. Returns (segments, language_code).
    end = start + duration (then clipped to the next start)."""
    from youtube_transcript_api import YouTubeTranscriptApi

    video_id = extract_video_id(video_id)
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):  # < 1.0
        listing = YouTubeTranscriptApi.list_transcripts(video_id)
    else:  # >= 1.0: instance API
        listing = YouTubeTranscriptApi().list(video_id)
    try:
        transcript = listing.find_manually_created_transcript(list(languages))
    except Exception:
        transcript = listing.find_generated_transcript(list(languages))
    fetched = transcript.fetch()
    raw = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else fetched
    items = [(r["start"], r["start"] + r["duration"], r["text"]) for r in raw]
    return _from_raw(items), transcript.language_code


def merge_short_segments(segments: list[dict], min_seconds: float = 3) -> list[dict]:
    """Join each segment shorter than min_seconds into the following one."""
    out: list[dict] = []
    pending: dict | None = None
    for seg in segments:
        if pending is None:
            pending = dict(seg)
        else:
            pending["end"] = seg["end"]
            pending["text"] = f"{pending['text']} {seg['text']}"
        if pending["end"] - pending["start"] >= min_seconds:
            out.append(pending)
            pending = None
    if pending is not None:
        if out:
            out[-1]["end"] = pending["end"]
            out[-1]["text"] += " " + pending["text"]
        else:
            out.append(pending)
    return out
