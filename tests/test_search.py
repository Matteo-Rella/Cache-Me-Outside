"""Search integration tests with deterministic embeddings and mocked coverage calls."""
from __future__ import annotations

import copy
import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from rewind import config
from rewind import search as retrieval
from tools import eval_search


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    fixture = config.LECTURES_DIR / "demo01/snippets.json"
    snippets = json.loads(fixture.read_text(encoding="utf-8"))
    folder = tmp_path / "demo01"
    folder.mkdir()
    source = folder / "snippets.json"
    source.write_text(json.dumps(snippets), encoding="utf-8")
    monkeypatch.setattr(config, "LECTURES_DIR", tmp_path)
    retrieval._get_model.cache_clear()
    retrieval._load_index.cache_clear()

    class FakeModel:
        def __init__(self):
            self.calls = []
            self.loads = []

        def encode(self, texts, **kwargs):
            self.calls.append(texts)
            vectors = []
            for text in texts:
                if text == "How do I bake a cake?":
                    vectors.append([-1, -1, -1])
                elif text == "What does Ax=b mean?":
                    vectors.append([0, 0, 5])
                elif text == "What is the coefficient matrix?":
                    vectors.append([0, 3, 0])
                else:
                    i = next(i for i, s in enumerate(snippets) if text.startswith(s["concept"]))
                    vectors.append(np.eye(3)[i] * (i + 2))
            return np.asarray(vectors, dtype=np.float32)

    model = FakeModel()

    def construct(name):
        model.loads.append(name)
        return model

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=construct))
    yield SimpleNamespace(folder=folder, source=source, snippets=snippets, model=model)
    retrieval._get_model.cache_clear()
    retrieval._load_index.cache_clear()


def test_obvious_question_ranks_fixture_snippet(corpus):
    results = retrieval.search("What is the coefficient matrix?")
    assert results[0]["snippet"] == corpus.snippets[1]
    assert results[0]["score"] == pytest.approx(1.0)
    assert len(results) == 3
    assert all(type(r["score"]) is float for r in results)
    assert retrieval.search("What does Ax=b mean?", top_k=1)[0]["snippet"]["id"] == "demo01-003"


def test_index_embeds_all_three_fields_and_saves_ids(corpus):
    assert retrieval.index_lecture("demo01") is None
    assert corpus.model.calls == [[
        "\n".join(s[key] for key in ("concept", "summary", "text")) for s in corpus.snippets
    ]]
    matrix = np.load(corpus.folder / "embeddings.npy", allow_pickle=False)
    np.testing.assert_allclose(np.linalg.norm(matrix, axis=1), 1)
    manifest = json.loads((corpus.folder / "embeddings.ids.json").read_text(encoding="utf-8"))
    assert manifest["snippet_ids"] == [s["id"] for s in corpus.snippets]
    assert manifest["model"] == config.EMBEDDING_MODEL


def test_model_and_embeddings_are_loaded_once(corpus, monkeypatch):
    load = retrieval.np.load
    reads = []

    def spy(*args, **kwargs):
        reads.append(args[0])
        return load(*args, **kwargs)

    monkeypatch.setattr(retrieval.np, "load", spy)
    retrieval.search("What is the coefficient matrix?")
    retrieval.search("What does Ax=b mean?")
    assert corpus.model.loads == [config.EMBEDDING_MODEL]
    assert len(reads) == 1
    assert len(corpus.model.calls) == 3  # one corpus batch, two queries


def test_changed_snippets_rebuild_index(corpus):
    retrieval.search("What does Ax=b mean?")
    corpus.snippets[2]["summary"] += " Updated explanation."
    corpus.source.write_text(json.dumps(corpus.snippets), encoding="utf-8")
    # Force an older index timestamp without sleeps or future source timestamps.
    old = corpus.source.stat().st_mtime_ns - 1_000_000
    os.utime(corpus.folder / "embeddings.npy", ns=(old, old))
    result = retrieval.search("What does Ax=b mean?")
    assert result[0]["snippet"]["summary"].endswith("Updated explanation.")
    assert len(corpus.model.calls) == 4  # corpus + query twice


@pytest.mark.parametrize("damage", ["ids", "array", "model", "hash"])
def test_invalid_indexes_are_rebuilt(corpus, damage):
    retrieval.index_lecture("demo01")
    ids_path = corpus.folder / "embeddings.ids.json"
    if damage == "ids":
        ids_path.unlink()
    elif damage == "array":
        (corpus.folder / "embeddings.npy").write_bytes(b"not an npy file")
    else:
        manifest = json.loads(ids_path.read_text(encoding="utf-8"))
        manifest["model" if damage == "model" else "snippets_sha256"] = "outdated"
        ids_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert retrieval.search("What does Ax=b mean?")[0]["snippet"]["id"] == "demo01-003"
    assert len(corpus.model.calls) == 3


def test_index_all_and_search_include_folders_without_meta(corpus):
    other = config.LECTURES_DIR / "other"
    other.mkdir()
    snippets = copy.deepcopy(corpus.snippets[:1])
    snippets[0].update(id="other-001", lecture_id="other")
    (other / "snippets.json").write_text(json.dumps(snippets), encoding="utf-8")
    retrieval.index_all()
    assert (other / "embeddings.npy").exists()
    results = retrieval.search("What does Ax=b mean?", top_k=10)
    assert len(results) == 4
    assert any(r["snippet"]["lecture_id"] == "other" for r in results)
    assert results[0]["snippet"]["id"] == "demo01-003"


def test_unrelated_question_rejected_before_llm(corpus, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Below-threshold questions must not call the LLM")

    monkeypatch.setattr(retrieval, "ask_json", unexpected)
    result = retrieval.answer("How do I bake a cake?")
    assert result["status"] == "not_covered"
    assert result["best"] is None
    assert result["alternatives"] == []
    assert result["score"] < config.SIMILARITY_THRESHOLD


def test_llm_rejects_related_but_unexplained_question(corpus, monkeypatch):
    monkeypatch.setattr(retrieval, "ask_json", lambda *a, **kw: {"best_index": None, "reason": "Not explained"})
    result = retrieval.answer("What does Ax=b mean?")
    assert result == {"status": "not_covered", "best": None, "alternatives": [], "score": 1.0}


def test_llm_can_choose_another_candidate(corpus, monkeypatch):
    candidates = [
        {"snippet": s, "score": score} for s, score in zip(corpus.snippets, [0.9, 0.8, 0.7])
    ]
    monkeypatch.setattr(retrieval, "search", lambda question, top_k: candidates)

    def check(system, user, max_tokens):
        assert system == retrieval.COVERAGE_PROMPT
        payload = json.loads(user)
        assert payload["question"] == "What does Ax=b mean?"
        assert [c["index"] for c in payload["candidates"]] == [0, 1, 2]
        assert payload["candidates"][2]["text"] == corpus.snippets[2]["text"]
        return {"best_index": 2, "reason": "This excerpt explains the notation"}

    monkeypatch.setattr(retrieval, "ask_json", check)
    result = retrieval.answer("What does Ax=b mean?")
    assert result == {
        "status": "found", "best": corpus.snippets[2],
        "alternatives": corpus.snippets[:2], "score": 0.7,
    }


@pytest.mark.parametrize("index", [-1, 3, True, "0", 1.5])
def test_invalid_llm_index_raises(corpus, monkeypatch, index):
    monkeypatch.setattr(retrieval, "ask_json", lambda *a, **kw: {"best_index": index, "reason": "bad"})
    with pytest.raises(ValueError, match="invalid best_index"):
        retrieval.answer("What does Ax=b mean?")


def test_threshold_boundary_is_included(corpus, monkeypatch):
    monkeypatch.setattr(retrieval, "search", lambda *a, **kw: [
        {"snippet": corpus.snippets[0], "score": config.SIMILARITY_THRESHOLD},
    ])
    monkeypatch.setattr(retrieval, "ask_json", lambda *a, **kw: {"best_index": 0, "reason": "covered"})
    assert retrieval.answer("Question")["status"] == "found"


def test_empty_corpus_and_blank_questions_need_no_model(corpus):
    assert retrieval.search(" ") == []
    assert retrieval.search("question", top_k=0) == []
    corpus.source.write_text("[]", encoding="utf-8")
    assert retrieval.search("question") == []
    assert retrieval.answer("question") == {
        "status": "not_covered", "best": None, "alternatives": [], "score": 0.0,
    }
    corpus.source.unlink()
    assert retrieval.search("question") == []
    assert not corpus.model.calls


def test_eval_reports_ranking_and_gap_counts(corpus, monkeypatch, capsys, tmp_path):
    path = tmp_path / "eval.json"
    path.write_text(json.dumps([
        {"question": "What does Ax=b mean?", "expected_snippet_id": "demo01-003"},
        {"question": "How do I bake a cake?", "expected_snippet_id": None},
    ]), encoding="utf-8")
    monkeypatch.setattr(retrieval, "ask_json", lambda *a, **kw: {"best_index": 0, "reason": "covered"})
    threshold = config.SIMILARITY_THRESHOLD
    assert eval_search.main([str(path), "--threshold", "0.4"]) == 0
    output = capsys.readouterr().out
    assert "Threshold only" in output and "With LLM coverage check" in output
    assert "hit@1: 100.0% (1/1)" in output
    assert "hit@3 (retrieval candidates): 100.0% (1/1)" in output
    assert "not_covered caught: 1/1" in output
    assert "not_covered precision: 100.0% (1/1)" in output
    assert config.SIMILARITY_THRESHOLD == threshold


def test_eval_skip_llm(corpus, monkeypatch, capsys, tmp_path):
    path = tmp_path / "eval.json"
    path.write_text('[{"question": "What does Ax=b mean?", "expected_snippet_id": "demo01-003"}]')
    monkeypatch.setattr(retrieval, "answer", lambda *a: pytest.fail("--skip-llm called answer"))
    assert eval_search.main([str(path), "--skip-llm"]) == 0
    assert "With LLM" not in capsys.readouterr().out
