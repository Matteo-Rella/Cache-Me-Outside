"""Evaluate rewind.chat on the 15-item economics/psychology set (tests/chat_eval.json).

Default mode is OFFLINE structural evaluation:
  python tools/eval_chat.py [tests/chat_eval.json]
Checks prompt assembly, budget limits, policy instructions, citation validity/stripping,
and fixture replies through CitationFilter/run_turn. This verifies structural contracts
ONLY and does NOT measure real LLM groundedness or claim-support quality.

Explicit bounded live mode (never run automatically; requires API key/model):
  python tools/eval_chat.py --live --max-calls 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rewind import chat, config, lectures


def _check_item(item: dict, live: bool) -> dict:
    """Build the real prompt and check structural/policy/citation invariants for one eval item."""
    orig_load_slides = chat.load_slides
    fixture_slides = item.get("fixture_slides")
    if fixture_slides is not None:
        chat.load_slides = lambda lid, fs=fixture_slides: list(fs) if lid == item["lecture_id"] else []
    try:
        system, history, ctx = chat.build_prompt(
            item["snippet_id"],
            [{"role": "user", "content": item["question"]}],
            policy=item.get("policy", "strict"),
        )
    finally:
        chat.load_slides = orig_load_slides

    snippet = lectures.get_snippet(item["snippet_id"])
    assert snippet is not None, f"Missing fixture snippet {item['snippet_id']}"

    if live:
        from rewind import llm

        stream = llm.chat(system, history, max_tokens=chat.MAX_TOKENS, stream=True, purpose="runtime")
        mode_tag = "live"
    else:
        reply = item["mock_reply"]
        # Split into small chunks to exercise streaming boundaries too.
        stream = [reply[i : i + 17] for i in range(0, len(reply), 17)]
        mode_tag = "offline-fixture"

    events = list(chat.run_turn(stream, ctx))
    shown = "".join(e["text"] for e in events if e["type"] == "text")
    done = events[-1]

    checks: list[str] = []
    # Prompt structural checks
    if snippet["concept"] not in system:
        checks.append("missing clip concept in prompt")
    if item["policy"] == "strict" and "This isn't in your course materials." not in system:
        checks.append("strict policy missing from prompt")
    if item["policy"] == "labelled" and "Not from your course materials:" not in system:
        checks.append("labelled policy missing from prompt")
    if fixture_slides:
        for fs in fixture_slides:
            if f"[Slide {fs['n']}]" not in system:
                checks.append(f"fixture slide {fs['n']} missing from prompt")
    if len(system) + sum(len(m["content"]) for m in history) > chat._budget() + 50:
        checks.append("prompt exceeded budget")

    # Output structural / citation / policy checks
    if chat.META_PREFIX in shown or chat.META_SUFFIX in shown:
        checks.append("hidden META marker leaked")
    if done.get("dropped_citations"):
        checks.append(f"dropped invalid citations: {done['dropped_citations']}")

    exp = item["expected"]
    if done["outside_materials"] != exp["outside_materials"]:
        checks.append(f"outside_materials={done['outside_materials']} != {exp['outside_materials']}")
    if done["homework"] != exp["homework"]:
        checks.append(f"homework={done['homework']} != {exp['homework']}")
    if exp["must_cite"] and not done["citations"]:
        checks.append("expected at least one valid citation")
    if exp["needs_slide"] and not any(c["kind"] == "slide" for c in done["citations"]):
        checks.append("expected a slide citation")
    if exp["outside_materials"]:
        low = shown.lower()
        if item["policy"] == "strict" and "isn't in your course materials" not in low:
            checks.append("strict refusal wording missing")
        if item["policy"] == "labelled" and not low.startswith("not from your course materials:"):
            checks.append("labelled prefix missing")

    return {
        "id": item["id"],
        "category": item["category"],
        "policy": item["policy"],
        "mode": mode_tag,
        "ok": not checks,
        "issues": checks,
        "citations": done["citations"],
        "outside_materials": done["outside_materials"],
        "homework": done["homework"],
        "reply": shown.strip(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=config.ROOT_DIR / "tests" / "chat_eval.json")
    parser.add_argument("--live", action="store_true",
                        help="Send requests to the configured live LLM (explicit opt-in only)")
    parser.add_argument("--max-calls", type=int, default=5,
                        help="Hard cap on live LLM calls when --live is passed (default 5)")
    args = parser.parse_args(argv)

    items = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        parser.error("Expected a non-empty JSON list in chat_eval.json")
    if args.max_calls <= 0:
        parser.error("--max-calls must be positive")

    if args.live:
        from rewind import llm

        if not llm.available():
            print("Live mode requires configured API credentials/model (llm.available() is False).", file=sys.stderr)
            return 2
        items = items[: min(args.max_calls, len(items))]
        print(f"MODE: LIVE bounded evaluation ({len(items)}/{args.max_calls} max calls).")
        print("NOTE: Citation validity checks range/existence only; manually inspect replies for claim support.")
    else:
        print("MODE: OFFLINE structural evaluation (fixtures, prompts, policies, and citation validity ONLY).")
        print("NOTE: Offline checks do NOT establish real LLM groundedness or model output quality.")

    results = [_check_item(item, live=args.live) for item in items]
    passed = sum(1 for r in results if r["ok"])
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        cites = ", ".join(
            f"[{chat.fmt_time(c['t'])}]" if c["kind"] == "time" else f"[Slide {c['n']}]"
            for c in r["citations"]
        ) or "-"
        print(f"  {r['id']} [{r['category']:<10} {r['policy']:<8}] {status}  cites={cites}"
              + (f"  issues={r['issues']}" if r["issues"] else ""))

    print(f"\nStructural checks passed: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
