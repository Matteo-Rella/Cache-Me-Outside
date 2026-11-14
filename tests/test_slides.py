"""Generated PDF/PPTX and temporary lecture tests; no downloads, AI or live data."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import zipfile

import numpy as np
import pymupdf
import pytest
from pptx import Presentation
from pptx.util import Inches

from rewind import config, search, slides


@pytest.fixture
def lecture(tmp_path, monkeypatch):
    root = tmp_path / "lectures"
    folder = root / "test01"
    folder.mkdir(parents=True)
    monkeypatch.setattr(config, "LECTURES_DIR", root)
    monkeypatch.setattr(search.llm, "available", lambda: False)
    return folder


def make_pdf(path: Path):
    with pymupdf.open() as document:
        for title, text in [("Coefficient matrix", "The matrix collects coefficients of unknowns."),
                            ("Neurons", "Neurons send signals to one another."),
                            ("Elasticity", "Elasticity measures responsiveness to a price change.")]:
            page = document.new_page(width=720, height=405)
            page.insert_text((40, 55), title, fontsize=24)
            page.insert_text((40, 105), text, fontsize=15)
        document.save(path)
    return path


def make_pptx(path: Path):
    deck = Presentation()
    for title, body in [("Coefficient matrix", "matrix coefficients"), ("Neurons", "neuron messages"),
                        ("Elasticity", "price responsiveness")]:
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
    table = deck.slides[0].shapes.add_table(1, 2, Inches(1), Inches(3), Inches(4), Inches(1)).table
    table.cell(0, 0).text, table.cell(0, 1).text = "x", "y"
    group = deck.slides[0].shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(5), Inches(3), Inches(1)).text = "Grouped explanation"
    deck.save(path)
    return path


@pytest.fixture
def encode(monkeypatch):
    calls = []

    def fake(texts):
        calls.append(texts)
        vectors = []
        for text in texts:
            text = text.casefold()
            if "matrix" in text:
                vectors.append([1, 0, 0])
            elif "neuron" in text:
                vectors.append([0, 1, 0])
            elif "elasticity" in text:
                vectors.append([0, 0, 1])
            else:
                vectors.append([-1, -1, -1])
        return search._normalize(np.asarray(vectors))

    monkeypatch.setattr(search, "_encode", fake)
    search._load_index.cache_clear()
    search._load_slide_index.cache_clear()
    yield calls
    search._load_index.cache_clear()
    search._load_slide_index.cache_clear()


def test_pdf_text_and_three_page_images_preserve_source(lecture, tmp_path):
    source = make_pdf(tmp_path / "three-pages.pdf")
    original = source.read_bytes()
    directory = lecture / "slides"
    directory.mkdir()
    (directory / "unrelated.txt").write_text("keep")
    assert slides.ingest_slides("test01", source) == 3
    records = slides.load_slides("test01")
    assert [s["n"] for s in records] == [1, 2, 3]
    assert [s["title"] for s in records] == ["Coefficient matrix", "Neurons", "Elasticity"]
    for record in records:
        assert record["text"]
        image = pymupdf.Pixmap(str(lecture / record["image"]))
        assert abs(image.width - 1280) <= 1
        assert image.height == 720
    assert source.read_bytes() == original
    assert (directory / "unrelated.txt").read_text() == "keep"
    assert not list(lecture.glob(".slides-build-*"))


def test_bad_pdf_does_not_replace_existing_slides(lecture, tmp_path):
    source = make_pdf(tmp_path / "good.pdf")
    slides.ingest_slides("test01", source)
    before = (lecture / "slides/slides.json").read_bytes()
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a PDF")
    with pytest.raises(ValueError):
        slides.ingest_slides("test01", bad)
    assert (lecture / "slides/slides.json").read_bytes() == before


def test_pdf_missing_dependency_is_honest(lecture, tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "slides.pdf")
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    with pytest.raises(RuntimeError, match="requires PyMuPDF"):
        slides.ingest_slides("test01", source)
    assert not (lecture / "slides/slides.json").exists()


def test_pptx_text_groups_tables_without_converter(lecture, tmp_path, monkeypatch):
    source = make_pptx(tmp_path / "deck.pptx")
    original = source.read_bytes()
    monkeypatch.setattr(slides, "_convert_pptx", lambda *args: None)
    with pytest.warns(RuntimeWarning, match="Export the deck as PDF"):
        assert slides.ingest_slides("test01", source) == 3
    records = slides.load_slides("test01")
    assert all(s["image"] is None for s in records)
    assert "x | y" in records[0]["text"] and "Grouped explanation" in records[0]["text"]
    assert source.read_bytes() == original


def test_optional_pptx_conversion_uses_pdf_previews(lecture, tmp_path, monkeypatch):
    source = make_pptx(tmp_path / "deck.pptx")
    pdf = make_pdf(tmp_path / "converted.pdf")
    monkeypatch.setattr(slides, "_convert_pptx", lambda *args: pdf)
    assert slides.ingest_slides("test01", source) == 3
    records = slides.load_slides("test01")
    assert all((lecture / s["image"]).is_file() for s in records)
    assert "Grouped explanation" in records[0]["text"]  # PPTX text, not converter text.


@pytest.mark.parametrize("lecture_id", ["../other", "test01/other", "test01\\other", "C:other", "", ".."])
def test_lecture_traversal_rejected(lecture, lecture_id):
    with pytest.raises(ValueError):
        slides.load_slides(lecture_id)


@pytest.mark.parametrize("image", ["../secret.png", "slides/../secret.png", "/slides/x.png", "slides\\x.png"])
def test_slide_image_traversal_rejected(lecture, image):
    directory = lecture / "slides"
    directory.mkdir()
    (directory / "slides.json").write_text(json.dumps([{"n": 1, "title": "t", "text": "t", "image": image}]))
    with pytest.raises(ValueError):
        slides.load_slides("test01")


def test_archive_traversal_and_external_assets(lecture, tmp_path):
    bad = tmp_path / "unsafe.pptx"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../outside", "unsafe")
    with pytest.raises(ValueError, match="unsafe paths"):
        slides._pptx_is_safe(bad)
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("ppt/_rels/slide1.xml.rels", '<Relationships><Relationship TargetMode="External" '
                         'Type="image" Target="https://example.com/image.png" /></Relationships>')
    assert slides._pptx_is_safe(bad) is False


def test_alignment_preserves_every_unrelated_field(lecture, tmp_path, encode):
    slides.ingest_slides("test01", make_pdf(tmp_path / "deck.pdf"))
    snippets = [{"id": "test01-001", "lecture_id": "test01", "concept": "matrix",
                 "summary": "matrix", "text": "matrix", "start": 0, "end": 60,
                 "questions": [{"answer": 2}], "review": {"note": "preserve"}},
                {"id": "test01-002", "lecture_id": "test01", "concept": "neuron",
                 "summary": "neuron", "text": "neuron", "start": 60, "end": 120, "questions": []}]
    (lecture / "snippets.json").write_text(json.dumps(snippets))
    assert slides.align("test01") == 2
    result = json.loads((lecture / "snippets.json").read_text())
    assert [s["slides"] for s in result] == [[1], [2]]
    for expected, actual in zip(snippets, result):
        assert {k: v for k, v in actual.items() if k != "slides"} == expected


def test_no_slides_alignment_needs_no_model_and_clears_stale_links(lecture, monkeypatch):
    assert slides.load_slides("test01") == []
    assert slides.align("test01") == 0
    path = lecture / "snippets.json"
    snippets = [{"id": "a", "custom": {"keep": True}}, {"id": "b", "slides": [3]}]
    path.write_text(json.dumps(snippets))
    monkeypatch.setattr(search, "_encode", lambda *args: pytest.fail("No slides must not load a model"))
    assert slides.align("test01") == 0
    assert json.loads(path.read_text()) == [{"id": "a", "custom": {"keep": True}}, {"id": "b", "slides": []}]


def test_alignment_does_not_overwrite_concurrent_edits(lecture, tmp_path, encode, monkeypatch):
    slides.ingest_slides("test01", make_pdf(tmp_path / "deck.pdf"))
    source = lecture / "snippets.json"
    source.write_text('[{"id":"a","concept":"matrix","summary":"matrix","text":"matrix"}]')
    original = search._encode

    def changed(texts):
        current = json.loads(source.read_text())
        current[0]["another_worker"] = "keep"
        source.write_text(json.dumps(current))
        return original(texts)

    monkeypatch.setattr(search, "_encode", changed)
    with pytest.raises(RuntimeError, match="changed during alignment"):
        slides.align("test01")
    assert json.loads(source.read_text())[0]["another_worker"] == "keep"


def test_alignment_keeps_at_most_two_above_threshold(lecture, tmp_path, monkeypatch):
    slides.ingest_slides("test01", make_pdf(tmp_path / "deck.pdf"))
    source = lecture / "snippets.json"
    source.write_text('[{"id":"a","concept":"topic","summary":"topic","text":"topic"}]')
    monkeypatch.setattr(search, "_encode", lambda texts: np.tile([1.0, 0.0], (len(texts), 1)))
    assert slides.align("test01") == 1
    assert json.loads(source.read_text())[0]["slides"] == [1, 2]
