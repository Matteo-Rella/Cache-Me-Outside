"""One command from a lecture video file to a searchable lecture.

    python -m rewind.ingest --video C:\\path\\lecture.mp4 --lecture-id ai01 --title "..." \\
        --course "..." --lecture-number 1 [--transcript transcript.json] [--raw raw_llm_output.json]

Inputs: a local video file, lecture metadata, optionally a transcript.json (contract in AGENTS.md)
and optionally the raw JSON an LLM returned for the snippet prompt.
Outputs in data/lectures/<lecture_id>/: video.<ext>, meta.json (video_url "media/<id>/video.<ext>"),
transcript.json, snippets.json, embeddings.npy. Prints a summary.

Steps:
1. Copy the video and write meta.json.
2. Transcript: --transcript (validated), else faster-whisper "small" (CUDA if available), else stop.
3. Snippets: LLM via the snippet maker if available; else --raw through the snippet maker's
   import path; else write snippet_prompt.md for a manual run and stop.
4. Index with search.index_lecture.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from rewind import lectures, transcript_io

APP_URL = "http://localhost:8000/"
WHISPER_MODEL = "small"
VIDEO_EXTENSIONS = {".mp4", ".webm", ".m4v", ".mov", ".mkv", ".ogv", ".mp3", ".m4a", ".wav"}
_LECTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PROMPT_FILE = "snippet_prompt.md"


class IngestError(Exception):
    """A problem the user can fix; printed without a traceback."""


# ---------------------------------------------------------------- step 1: video + meta


def copy_video(video: Path, lecture_id: str) -> Path:
    """Copy the video into the lecture folder as video.<ext>. Skips the copy if identical in size."""
    if not video.is_file():
        raise IngestError(f"Video not found: {video}")
    ext = video.suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        raise IngestError(f"Unsupported video type {ext!r}; use one of {sorted(VIDEO_EXTENSIONS)}")
    dest = lectures.lecture_dir(lecture_id) / f"video{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.resolve() == video.resolve():
        return dest
    if dest.exists() and dest.stat().st_size == video.stat().st_size:
        print(f"Video already in place: {dest}")
        return dest
    print(f"Copying video to {dest} ...")
    shutil.copyfile(video, dest)
    return dest


def write_meta(lecture_id: str, video_path: Path, title: str | None, course: str | None,
               lecture_number: int | None) -> dict:
    meta = {
        "lecture_id": lecture_id,
        "title": title or lecture_id,
        "course": course or "",
        "lecture_number": lecture_number,
        "video_url": f"media/{lecture_id}/{video_path.name}",
    }
    lectures.save_meta(lecture_id, meta)
    return meta


# ---------------------------------------------------------------- step 2: transcript


def transcribe_whisper(video: Path, model_name: str = WHISPER_MODEL, out=None) -> list[dict]:
    """Transcribe with faster-whisper (device auto: CUDA if available). Raises ImportError if missing."""
    out = out or sys.stdout
    from faster_whisper import WhisperModel  # optional dependency

    print(f"Loading faster-whisper model {model_name!r} (device auto) ...", file=out, flush=True)
    model = WhisperModel(model_name, device="auto", compute_type="default")
    segments, info = model.transcribe(str(video), vad_filter=True)
    total = float(getattr(info, "duration", 0) or 0)
    print(f"Detected language: {getattr(info, 'language', '?')}; audio {_fmt(total)}", file=out, flush=True)
    collected, last_pct = [], -10
    for seg in segments:  # generator: transcription happens while iterating
        collected.append(seg)
        pct = int(100 * seg.end / total) if total else 0
        if pct >= last_pct + 10:
            last_pct = pct - pct % 10
            print(f"  transcribing ... {pct:3d}%  ({_fmt(seg.end)})", file=out, flush=True)
    print("  transcribing ... 100%", file=out, flush=True)
    return transcript_io.from_whisper(collected)


def get_transcript(lecture_id: str, video: Path, transcript: Path | None,
                   transcriber: Callable[[Path], list[dict]] | None = None) -> list[dict]:
    """Validated transcript segments, saved to the lecture folder."""
    if transcript is not None:
        try:
            segments = transcript_io.load_transcript(transcript)
        except FileNotFoundError:
            raise IngestError(f"Transcript not found: {transcript}") from None
        except (ValueError, json.JSONDecodeError) as err:
            raise IngestError(f"Transcript {transcript} doesn't follow the contract: {err}") from None
        print(f"Using transcript {transcript} ({len(segments)} segments)")
    else:
        transcriber = transcriber or transcribe_whisper
        try:
            segments = transcriber(video)
        except ImportError:
            raise IngestError(
                "No transcript given and faster-whisper isn't installed.\n"
                "Either pass --transcript path\\to\\transcript.json (the teammate's transcriber output),\n"
                "or install it: pip install faster-whisper"
            ) from None
        except Exception as err:  # noqa: BLE001 - surface any transcriber failure clearly
            raise IngestError(f"Transcription failed: {type(err).__name__}: {err}") from err
    if not segments:
        raise IngestError("The transcript is empty.")
    lectures.save_transcript(lecture_id, segments)
    return segments


# ---------------------------------------------------------------- step 3: snippets


def llm_available() -> bool:
    """llm.available() if it exists; otherwise: are ANTHROPIC_API_KEY and ANTHROPIC_MODEL set?"""
    from rewind import llm

    fn = getattr(llm, "available", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False
    import os

    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip() and os.environ.get("ANTHROPIC_MODEL", "").strip())


def import_raw_snippets(raw_path: Path, segments: list[dict], lecture_id: str) -> list[dict]:
    """Raw LLM output file -> snippets, through the snippet maker's import path.

    Uses snippet_maker.import_raw(raw_path, segments, lecture_id) when it exists (being added by
    another terminal), else the snippet maker's own finalize() on the parsed JSON.
    """
    from rewind import snippet_maker as sm

    fn = getattr(sm, "import_raw", None)
    if callable(fn):
        return fn(raw_path, segments, lecture_id)
    try:
        text = Path(raw_path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise IngestError(f"Raw LLM output not found: {raw_path}") from None
    from rewind.llm import parse_json

    try:
        data = parse_json(text)
        raw = sm._as_list(data)
    except (ValueError, json.JSONDecodeError) as err:
        raise IngestError(f"Couldn't read {raw_path} as the LLM's JSON snippet list: {err}") from None
    return sm.finalize(raw, segments, lecture_id)


def export_prompt(lecture_id: str, segments: list[dict], title: str | None) -> Path:
    """Write the exact prompt(s) the snippet maker would send, for a manual run in Claude."""
    from rewind import snippet_maker as sm

    calls: list[tuple[str, str]] = []

    def capture(system: str, user: str, **_: Any) -> list:
        calls.append((system, user))
        return []

    sm.request_raw_snippets(segments, title=title, ask=capture)
    parts = []
    for i, (system, user) in enumerate(calls, 1):
        head = f"# Part {i} of {len(calls)}\n\n" if len(calls) > 1 else ""
        parts.append(f"{head}## Instructions\n\n{system}\n\n## Input\n\n{user}\n")
    path = lectures.lecture_dir(lecture_id) / PROMPT_FILE
    path.write_text("\n\n".join(parts), encoding="utf-8")
    return path


def make_lecture_snippets(lecture_id: str, segments: list[dict], title: str | None,
                          raw: Path | None, ask: Callable[..., Any] | None = None) -> list[dict] | None:
    """Snippets via LLM, or --raw import. None if neither was possible (prompt exported instead)."""
    from rewind import snippet_maker as sm

    if raw is not None:
        print(f"Importing raw LLM output from {raw}")
        snippets = import_raw_snippets(raw, segments, lecture_id)
    elif ask is not None or llm_available():
        print("Making snippets with the LLM ...", flush=True)
        snippets = sm.make_snippets(segments, lecture_id, title=title, ask=ask)
    else:
        return None
    if not snippets:
        raise IngestError("No valid snippets came out of the snippet step.")
    lectures.save_snippets(lecture_id, snippets)
    return snippets


# ---------------------------------------------------------------- CLI


def _fmt(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def run(args: argparse.Namespace, transcriber=None, ask=None, indexer=None) -> int:
    lecture_id = args.lecture_id
    if not _LECTURE_ID.match(lecture_id):
        raise IngestError("--lecture-id may only use letters, digits, '-' and '_'.")
    folder = lectures.lecture_dir(lecture_id)
    if (folder / "snippets.json").exists() and not args.force:
        raise IngestError(f"{folder} already has snippets.json. Use --force to rebuild it.")

    video = copy_video(Path(args.video), lecture_id)
    write_meta(lecture_id, video, args.title, args.course, args.lecture_number)
    segments = get_transcript(lecture_id, Path(args.video),
                              Path(args.transcript) if args.transcript else None, transcriber)

    snippets = make_lecture_snippets(lecture_id, segments, args.title,
                                     Path(args.raw) if args.raw else None, ask=ask)
    if snippets is None:
        prompt = export_prompt(lecture_id, segments, args.title)
        transcript_path = folder / "transcript.json"
        print(
            "\nNo LLM available (no ANTHROPIC_API_KEY/ANTHROPIC_MODEL), and no --raw given.\n"
            f"1. Open {prompt} and paste it into Claude (each part separately if there are several).\n"
            "2. Save Claude's JSON reply (all parts combined into one JSON array) as e.g. raw.json.\n"
            "3. Rerun, reusing the transcript so there's no new transcription:\n"
            f"   python -m rewind.ingest --video \"{args.video}\" --lecture-id {lecture_id}"
            f" --transcript \"{transcript_path}\" --raw raw.json"
            + (f" --title \"{args.title}\"" if args.title else "")
            + (f" --course \"{args.course}\"" if args.course else "")
            + (f" --lecture-number {args.lecture_number}" if args.lecture_number is not None else "")
        )
        return 2

    print("Indexing for search ...", flush=True)
    if indexer is None:
        from rewind import search

        indexer = search.index_lecture
    indexer(lecture_id)

    duration = segments[-1]["end"] - segments[0]["start"]
    print(
        f"\nDone: {lecture_id}\n"
        f"  duration  {_fmt(segments[-1]['end'])} (speech {_fmt(duration)})\n"
        f"  segments  {len(segments)}\n"
        f"  snippets  {len(snippets)}\n"
        f"  video     {video}\n"
        f"Open {APP_URL}  (start the server with: uvicorn api:app --port 8000)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m rewind.ingest", description=__doc__.split("\n\n")[0])
    p.add_argument("--video", required=True, help="local lecture video file")
    p.add_argument("--lecture-id", required=True)
    p.add_argument("--title")
    p.add_argument("--course")
    p.add_argument("--lecture-number", type=int)
    p.add_argument("--transcript", help="transcript.json following the contract (skips transcription)")
    p.add_argument("--raw", help="raw LLM output for the snippet prompt (skips the LLM call)")
    p.add_argument("--force", action="store_true", help="rebuild a lecture that already has snippets")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except IngestError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
