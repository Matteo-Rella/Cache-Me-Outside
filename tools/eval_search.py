"""Evaluate search on JSON questions; print ranking and gap metrics.

Run: python tools/eval_search.py [tests/search_eval.json] [--skip-llm]
The default compares threshold-only and LLM-checked answers; --skip-llm needs
no Anthropic credentials. --threshold permits tuning without editing config.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support direct execution from any directory as well as python -m tools.eval_search.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rewind import config
from rewind import search as retrieval


def _report(label: str, rows: list[dict]) -> None:
    covered = [row for row in rows if row["expected"] is not None]
    gaps = [row for row in rows if row["expected"] is None]
    hit1 = sum(row["predicted"] == row["expected"] for row in covered)
    hit3 = sum(row["expected"] in row["top3"] for row in covered)
    caught = sum(row["predicted"] is None for row in gaps)
    rejected = sum(row["predicted"] is None for row in rows)

    def metric(numerator, denominator):
        return f"{numerator / denominator:.1%} ({numerator}/{denominator})" if denominator else "n/a (0/0)"

    print(label)
    print(f"  hit@1: {metric(hit1, len(covered))}")
    print(f"  hit@3 (retrieval candidates): {metric(hit3, len(covered))}")
    print(f"  not_covered caught: {caught}/{len(gaps)}")
    print(f"  not_covered precision: {metric(caught, rejected)}")
    print(f"  not_covered recall: {metric(caught, len(gaps))}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=config.ROOT_DIR / "tests/search_eval.json")
    parser.add_argument("--skip-llm", action="store_true", help="Only evaluate the similarity threshold")
    parser.add_argument("--threshold", type=float, default=config.SIMILARITY_THRESHOLD)
    args = parser.parse_args(argv)
    items = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("question"), str)
        or "expected_snippet_id" not in item
        or (item["expected_snippet_id"] is not None and not isinstance(item["expected_snippet_id"], str))
        for item in items
    ):
        parser.error("Expected a list of {question: str, expected_snippet_id: str or null}.")
    if not -1 <= args.threshold <= 1:
        parser.error("--threshold must be between -1 and 1.")
    baseline, checked = [], []
    old_threshold = config.SIMILARITY_THRESHOLD
    config.SIMILARITY_THRESHOLD = args.threshold
    try:
        print(f"Similarity threshold: {args.threshold:.3f}")
        for item in items:
            candidates = retrieval.search(item["question"], top_k=3)
            score = candidates[0]["score"] if candidates else 0.0
            predicted = candidates[0]["snippet"]["id"] if candidates and score >= args.threshold else None
            row = {"expected": item["expected_snippet_id"], "top3": [r["snippet"]["id"] for r in candidates]}
            baseline.append({**row, "predicted": predicted})
            if not args.skip_llm:
                result = retrieval.answer(item["question"])
                checked.append({**row, "predicted": result["best"]["id"] if result["best"] else None})
            print(f"  {score:.3f} {predicted or 'not_covered'} | {item['question']}")
        _report("Threshold only", baseline)
        if not args.skip_llm:
            _report("With LLM coverage check", checked)
    finally:
        config.SIMILARITY_THRESHOLD = old_threshold
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
