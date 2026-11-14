"""Load and save lecture data under data/lectures/<lecture_id>/.

Files per lecture: meta.json, transcript.json, snippets.json (contracts in AGENTS.md).
"""
from __future__ import annotations

import json
from pathlib import Path

from rewind import config


def lecture_dir(lecture_id: str) -> Path:
    return config.LECTURES_DIR / lecture_id


def _read(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def list_lectures() -> list[str]:
    """Lecture ids (folder names that contain meta.json), sorted."""
    if not config.LECTURES_DIR.exists():
        return []
    return sorted(p.name for p in config.LECTURES_DIR.iterdir() if (p / "meta.json").exists())


def load_meta(lecture_id: str) -> dict:
    return _read(lecture_dir(lecture_id) / "meta.json")


def save_meta(lecture_id: str, meta: dict) -> None:
    _write(lecture_dir(lecture_id) / "meta.json", meta)


def load_transcript(lecture_id: str) -> list[dict]:
    from rewind import transcript_io

    return transcript_io.load_transcript(lecture_dir(lecture_id) / "transcript.json")


def save_transcript(lecture_id: str, segments: list[dict]) -> None:
    _write(lecture_dir(lecture_id) / "transcript.json", segments)


def load_snippets(lecture_id: str) -> list[dict]:
    path = lecture_dir(lecture_id) / "snippets.json"
    return _read(path) if path.exists() else []


def save_snippets(lecture_id: str, snippets: list[dict]) -> None:
    _write(lecture_dir(lecture_id) / "snippets.json", snippets)


def all_snippets() -> list[dict]:
    return [s for lid in list_lectures() for s in load_snippets(lid)]


def get_snippet(snippet_id: str) -> dict | None:
    lecture_id = snippet_id.rsplit("-", 1)[0]
    for s in load_snippets(lecture_id):
        if s["id"] == snippet_id:
            return s
    return None
