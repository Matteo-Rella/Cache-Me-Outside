"""Ingest PDF/PPTX slides, load their text/images, and align slide numbers to clips.

ingest_slides(lecture_id, path) -> page count; load_slides(id) -> slide dictionaries;
align(id) -> number of snippets with matches. Source assets are never rewritten.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import warnings
import zipfile
from xml.etree import ElementTree

import numpy as np

from rewind import config

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
ALIGNMENT_THRESHOLD = 0.45


def _folder(lecture_id: str) -> Path:
    if (not isinstance(lecture_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", lecture_id)
        or lecture_id.endswith(".")):
        raise ValueError("Invalid lecture id.")
    root = config.LECTURES_DIR.resolve()
    candidate = root / lecture_id
    folder = candidate.resolve()
    if candidate.is_symlink() or folder != candidate or not folder.is_relative_to(root) or folder == root:
        raise ValueError("Lecture path escapes the lectures directory.")
    return folder


def _slides_dir(folder: Path) -> Path:
    directory = folder / "slides"
    if directory.is_symlink() or directory.resolve() != directory or not directory.resolve().is_relative_to(folder):
        raise ValueError("Unsafe slides directory.")
    return directory


def _atomic_json(path: Path, value) -> None:
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlink.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".slides-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def load_slides(lecture_id) -> list[dict]:
    """Return validated slide records, or [] if absent; unsafe metadata raises ValueError."""
    folder = _folder(lecture_id)
    directory = _slides_dir(folder)
    path = directory / "slides.json"
    if path.is_symlink():
        raise ValueError("Unsafe slide manifest.")
    if not path.exists():
        return []
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list) or len(values) > MAX_PAGES:
        raise ValueError("Slide manifest must be a list with at most 500 pages.")
    seen = set()
    result = []
    for value in values:
        if (not isinstance(value, dict) or type(value.get("n")) is not int or value["n"] <= 0
            or value["n"] in seen or not isinstance(value.get("title"), str)
            or not isinstance(value.get("text"), str)):
            raise ValueError("Invalid slide number, title or text in slide manifest.")
        seen.add(value["n"])
        record = dict(value)
        image = record.get("image")
        if image is not None:
            if not isinstance(image, str):
                raise ValueError("Invalid slide image path.")
            parts = PurePosixPath(image).parts
            if (len(parts) != 2 or parts[0] != "slides" or not re.fullmatch(r"[\w.-]+", parts[1])
                or Path(parts[1]).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}):
                raise ValueError("Slide images must be plain files inside slides/.")
            target = directory / parts[1]
            if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Unsafe slide image path.")
            if not target.is_file():
                record["image"] = None  # Honest text-only state for missing previews.
        result.append(record)
    return result


def _pdf_pages(source: Path, stage: Path, digest: str) -> list[dict]:
    try:
        import pymupdf
    except ImportError:
        raise RuntimeError("PDF slide support requires PyMuPDF. Install requirements.txt.") from None
    try:
        document = pymupdf.open(stream=source.read_bytes(), filetype="pdf")
    except (RuntimeError, ValueError):
        raise ValueError("Could not read this PDF slide file.") from None
    with document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("Slides must be an unencrypted PDF.")
        if not 1 <= len(document) <= MAX_PAGES:
            raise ValueError("Slide PDFs must contain between 1 and 500 pages.")
        records = []
        for n, page in enumerate(document, 1):
            text = page.get_text("text", sort=True).strip()
            if len(text) > 100_000:
                raise ValueError("A slide contains too much text.")
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise ValueError("Invalid PDF page size.")
            zoom = min(1280 / page.rect.width, 4096 / page.rect.height)
            image = f"slide-{n:03}-{digest}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(stage / image)
            title = next((line.strip() for line in text.splitlines() if line.strip()), f"Slide {n}")[:200]
            records.append({"n": n, "title": title, "text": text, "image": f"slides/{image}"})
        return records


def _pptx_is_safe(source: Path) -> bool:
    """Bound archive expansion; do not ask LibreOffice to load active/external assets."""
    convert = True
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum(e.file_size for e in entries) > 200 * 1024 * 1024:
                raise ValueError("PPTX archive is too large to extract safely.")
            for entry in entries:
                parts = PurePosixPath(entry.filename).parts
                if ".." in parts or entry.filename.startswith(("/", "\\")) or "\\" in entry.filename or ":" in entry.filename:
                    raise ValueError("PPTX archive contains unsafe paths.")
                if entry.filename.endswith("vbaProject.bin") or "/embeddings/" in entry.filename:
                    convert = False
                if entry.filename.endswith(".rels"):
                    for relation in ElementTree.fromstring(archive.read(entry)):
                        if relation.get("TargetMode") == "External" and not relation.get("Type", "").endswith("/hyperlink"):
                            convert = False
    except (zipfile.BadZipFile, ElementTree.ParseError):
        raise ValueError("Could not read this PPTX slide file.") from None
    return convert


def _shape_text(shapes) -> list[str]:
    lines = []
    for shape in shapes:
        if getattr(shape, "has_text_frame", False):
            lines.append(shape.text_frame.text)
        if getattr(shape, "has_table", False):
            lines.extend(" | ".join(cell.text for cell in row.cells) for row in shape.table.rows)
        if hasattr(shape, "shapes"):
            lines.extend(_shape_text(shape.shapes))
    return lines


def _convert_pptx(source: Path, stage: Path) -> Path | None:
    executable = shutil.which("soffice") or shutil.which("soffice.exe")
    if not executable:
        candidate = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "LibreOffice/program/soffice.exe"
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        return None
    copied = stage / "input.pptx"
    shutil.copyfile(source, copied)
    profile = stage / "lo-profile"
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        subprocess.run([executable, f"-env:UserInstallation={profile.as_uri()}", "--headless",
                        "--nologo", "--norestore", "--convert-to", "pdf", "--outdir", str(stage), str(copied)],
                       check=True, capture_output=True, timeout=60, **options)
    except (OSError, subprocess.SubprocessError):
        return None
    output = stage / "input.pdf"
    return output if output.is_file() else None


def ingest_slides(lecture_id, path) -> int:
    """Extract text/previews into slides/ without modifying or moving the source file."""
    folder = _folder(lecture_id)
    if not folder.is_dir():
        raise FileNotFoundError("Lecture folder does not exist.")
    source = Path(path).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() not in {".pdf", ".pptx"}:
        raise ValueError("Slides must be a PDF or PPTX file.")
    if not 0 < source.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("Slide source must be nonempty and no larger than 50 MiB.")
    directory = _slides_dir(folder)
    if (directory / "slides.json").is_symlink():
        raise ValueError("Unsafe slide manifest.")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()[:16]
    with tempfile.TemporaryDirectory(prefix=".slides-build-", dir=folder) as work:
        stage = Path(work)
        snapshot = stage / ("source" + source.suffix.lower())
        snapshot.write_bytes(content)
        if source.suffix.lower() == ".pdf":
            records = _pdf_pages(snapshot, stage, digest)
        else:
            convert = _pptx_is_safe(snapshot)
            try:
                from pptx import Presentation
            except ImportError:
                raise RuntimeError("PPTX slide support requires python-pptx. Install requirements.txt.") from None
            deck = Presentation(snapshot)
            if not 1 <= len(deck.slides) <= MAX_PAGES:
                raise ValueError("PPTX slides must contain between 1 and 500 pages.")
            records = []
            for n, slide in enumerate(deck.slides, 1):
                text = "\n".join(line.strip() for line in _shape_text(slide.shapes) if line.strip())
                if len(text) > 100_000:
                    raise ValueError("A slide contains too much text.")
                title = slide.shapes.title.text.strip() if slide.shapes.title is not None else ""
                records.append({"n": n, "title": (title or text.split("\n")[0] or f"Slide {n}")[:200],
                                "text": text, "image": None})
            pdf = _convert_pptx(snapshot, stage) if convert else None
            if pdf:
                try:
                    previews = _pdf_pages(pdf, stage, digest)
                    if len(previews) == len(records):
                        for record, preview in zip(records, previews):
                            record["image"] = preview["image"]
                except (RuntimeError, ValueError):
                    pass  # Text remains valid even when optional rendering fails.
            if any(record["image"] is None for record in records):
                warnings.warn("PPTX text imported without page previews. Export the deck as PDF for slide images.",
                              RuntimeWarning, stacklevel=2)
        if not any(record["text"].strip() for record in records):
            warnings.warn("Slides have no extractable text; images are available but text search needs OCR.",
                          RuntimeWarning, stacklevel=2)
        directory.mkdir(exist_ok=True)
        # Content-addressed image names preserve old previews until metadata is
        # committed and avoid browser-cache collisions on replacement uploads.
        for record in records:
            if record["image"]:
                name = PurePosixPath(record["image"]).name
                target = directory / name
                if target.is_symlink():
                    raise ValueError("Unsafe slide image destination.")
                os.replace(stage / name, target)
        _atomic_json(directory / "slides.json", records)
    return len(records)


def align(lecture_id) -> int:
    """Set only each snippet's slides field to its best 1–2 matches above 0.45 cosine."""
    folder = _folder(lecture_id)
    source = folder / "snippets.json"
    if source.is_symlink():
        raise ValueError("Unsafe snippet file.")
    if not source.exists():
        return 0
    before = source.read_bytes()
    snippets = json.loads(before)
    slide_path = _slides_dir(folder) / "slides.json"
    slide_before = slide_path.read_bytes() if slide_path.exists() else None
    records = [s for s in load_slides(lecture_id) if s["text"].strip()]
    aligned = 0
    updated = [dict(snippet) for snippet in snippets]
    if records and snippets:
        from rewind import search

        texts = [s["title"] + "\n" + s["text"] for s in records]
        texts += ["\n".join(s.get(key, "") for key in ("concept", "summary", "text")) for s in snippets]
        vectors = search._encode(texts)
        if len(vectors) != len(texts):
            raise ValueError("Embedding count does not match alignment inputs.")
        scores = vectors[len(records):] @ vectors[:len(records)].T
        for snippet, row in zip(updated, scores):
            order = np.argsort(-row, kind="stable")
            snippet["slides"] = [records[i]["n"] for i in order[:2] if row[i] >= ALIGNMENT_THRESHOLD]
            aligned += bool(snippet["slides"])
    else:
        for snippet in updated:
            if "slides" in snippet:
                snippet["slides"] = []
    slide_after = slide_path.read_bytes() if slide_path.exists() else None
    if source.read_bytes() != before or slide_after != slide_before:
        raise RuntimeError("Snippets or slides changed during alignment; retry with current data.")
    if updated != snippets:
        _atomic_json(source, updated)
    return aligned
