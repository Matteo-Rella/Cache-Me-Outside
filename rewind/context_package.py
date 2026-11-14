"""Context package: make snippets with any AI chat or coding agent, no API key needed.

build_package(lecture_id) -> bytes        zip with START-HERE.md, PROMPT-part-N.md, AGENT.md,
                                            transcript.json, schema.json, meta.json, check.py
build_chat_prompt(lecture_id, part=None)  -> list[str], one paste-ready message per part
                                            (part is 1-based; None = all parts)
import_answer(lecture_id, raw_text)       -> {"ok", "snippets_count", "errors": [str], "fix_message": str}
    raw_text can be a bare JSON array or chat replies with prose and ```json fences around the JSON,
    several parts pasted one after another. On success the snippets go through the snippet maker's
    normal post-processing and are saved (plus raw_llm_output.json). On failure nothing is saved and
    fix_message is a ready-to-paste message for the AI.

CLI:
  python -m rewind.context_package build  <lecture_id> [out.zip]
  python -m rewind.context_package prompt <lecture_id> [part]
  python -m rewind.context_package import <lecture_id> <answer file>

The answer checks (check_answer and helpers) use only the standard library: the zip ships the same
source as check.py so a coding agent can validate its own output outside the repo.
"""
from __future__ import annotations

import inspect
import io
import json
import re
import sys
import zipfile

from rewind import lectures, snippet_maker, transcript_io

PART_MAX_CHARS = 60_000  # chat apps start truncating pasted messages beyond roughly this size
# Also cap lecture time per part: the reply has ~1.3k characters per snippet, and chat apps cut off
# replies at roughly 8k tokens. 30 minutes is ~20 snippets, ~27k characters of JSON.
PART_MAX_SECONDS = 30 * 60
LOOKAHEAD_SECONDS = 120  # each part (except the last) shows this much of the next part to finish its last topic
SECONDS_MIN = snippet_maker.MIN_SECONDS
SECONDS_MAX = snippet_maker.MAX_SECONDS

_JSON_RULE_API = "Return JSON only: an array, no prose, no code fences."
_JSON_RULE_CHAT = (
    "Return JSON only: one JSON array inside a single ```json code block. "
    "No text before or after the block."
)

EXAMPLE = [  # deliberately from an unrelated subject, so the AI copies the shape and not the content
    {
        "concept": "Le Chatelier's principle",
        "start": 412.0,
        "end": 498.5,
        "summary": "If a system at equilibrium is disturbed, it shifts to partly undo the change. "
        "Adding more reactant therefore pushes the reaction towards the products.",
        "questions": [
            {
                "q": "A gas reaction is at equilibrium and you remove some product. What happens next?",
                "options": [
                    "Nothing changes",
                    "The reaction makes more product",
                    "The reaction makes more reactant",
                    "The reaction stops",
                ],
                "answer": 1,
                "explanation": "The system shifts to partly undo the change, so removing product makes it form more product.",
            },
            {
                "q": "Why does adding reactant push the reaction towards the products?",
                "options": [
                    "Because the system shifts to partly undo the added reactant",
                    "Because the temperature rises",
                    "Because the products become unstable",
                    "Because the catalyst is used up",
                ],
                "answer": 0,
                "explanation": "The lecturer says a disturbed equilibrium shifts to counteract the change.",
            },
        ],
    }
]

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Rewind raw snippets (raw_llm_output.json)",
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "required": ["concept", "start", "end", "summary", "questions"],
        "properties": {
            "concept": {"type": "string", "minLength": 1, "description": "Short noun phrase a student would search for"},
            "start": {"type": "number", "description": "Seconds; timestamp of the transcript line where the topic starts"},
            "end": {"type": "number", "description": "Seconds; timestamp of the first transcript line after the snippet"},
            "summary": {"type": "string", "minLength": 1, "description": "Exactly 2 sentences, only what the professor said"},
            "questions": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "required": ["q", "options", "answer", "explanation"],
                    "properties": {
                        "q": {"type": "string", "minLength": 1},
                        "options": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "string", "minLength": 1}},
                        "answer": {"type": "integer", "minimum": 0, "maximum": 3, "description": "0-based index of the correct option"},
                        "explanation": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------- answer checks (stdlib only, shipped as check.py)


def mmss(seconds):
    seconds = int(round(seconds))
    return "%d:%02d" % divmod(seconds, 60) if seconds < 3600 else "%d:%02d:%02d" % (
        seconds // 3600, seconds % 3600 // 60, seconds % 60)


def extract_snippets(text, ignore=()):
    """Find the snippet JSON in a messy reply. Returns (items, errors).

    Accepts a bare array, ```json fenced blocks with prose around them, several replies pasted
    one after another, or an object like {"snippets": [...]}.
    Newest answer wins: each block replaces earlier snippets that start inside its own time span,
    and blocks with separate spans (parts, or "continue" replies) are appended. So the user can
    paste the whole chat, including old answers and corrections. A broken block is reported only
    if no valid block comes after it; otherwise it was superseded (a still-missing part is caught
    by the part check in check_answer). Items equal to one in `ignore` (the prompt's format example,
    when a whole chat is pasted) are skipped before merging.
    """
    import json as _json

    def num(v):
        if isinstance(v, bool):
            return None
        try:
            n = float(v)
            return None if n != n or abs(n) == float("inf") else n
        except (TypeError, ValueError):
            return None

    decoder = _json.JSONDecoder()
    skip = {_json.dumps(v, sort_keys=True) for v in ignore}
    blocks_found = []  # ("ok", items) or ("err", message), in order
    # Fences count only on their own line, so inline mentions like "a ```json block" are ignored.
    # "```json" always opens a new block (a cut-off reply may never have closed the previous one);
    # a bare "```" closes the open block, or opens one if none is open.
    blocks, current = [], None
    for line in text.splitlines():
        m = re.match(r"^[ \t]*```([a-zA-Z]*)[ \t]*$", line)
        if not m:
            if current is not None:
                current.append(line)
            continue
        if m.group(1) or current is None:
            if current is not None:
                blocks.append("\n".join(current))
            current = []
        else:
            blocks.append("\n".join(current))
            current = None
    if current is not None:
        blocks.append("\n".join(current))
    sources = blocks if blocks else [text]
    for src in sources:
        i, n = 0, len(src)
        while i < n:
            j = src.find("[", i)
            k = src.find("{", i)
            starts = [p for p in (j, k) if p != -1]
            if not starts:
                break
            p = min(starts)
            try:
                value, end = decoder.raw_decode(src, p)
            except _json.JSONDecodeError as err:
                if src[p] == "[" and re.match(r"\[\s*\{", src[p:]):
                    line = src.count("\n", 0, err.pos) + 1
                    blocks_found.append(("err", "The JSON could not be read (%s, line %d of that block). It may "
                                         "be cut off or have a missing comma or quote. Send that answer again, "
                                         "complete." % (err.msg, line)))
                    break
                i = p + 1
                continue
            if isinstance(value, dict) and isinstance(value.get("snippets"), list):
                value = value["snippets"]
            if isinstance(value, dict) and ("concept" in value and "start" in value):
                value = [value]
            if isinstance(value, list) and value and all(isinstance(v, dict) for v in value) and any(
                    "concept" in v for v in value):
                value = [v for v in value if _json.dumps(v, sort_keys=True) not in skip]
                if value:
                    blocks_found.append(("ok", value))
            i = end
    if not blocks_found:
        return [], ["I couldn't find a JSON array of snippets in the answer. Reply with the JSON "
                    "array inside one ```json code block."]
    items, errors = [], []
    for kind, payload in blocks_found:
        if kind == "err":
            errors.append(payload)
            continue
        errors = []  # a valid later block supersedes earlier broken ones
        starts = [num(v.get("start")) for v in payload if num(v.get("start")) is not None]
        ends = [num(v.get("end")) for v in payload if num(v.get("end")) is not None]
        if starts:
            lo, hi = min(starts), max(ends + starts)
            items = [v for v in items if num(v.get("start")) is None or not lo <= num(v.get("start")) <= hi]
        items.extend(payload)
    return items, errors


def _label(i, item):
    concept = item.get("concept") if isinstance(item, dict) else None
    return "Snippet %d%s" % (i + 1, ' ("%s")' % concept if concept else "")


def check_answer(items, t_min, t_max, parts=None, min_s=20, max_s=150, max_errors=25):
    """Check raw snippets against the contract. Returns a list of error strings ('' list = fine).

    t_min/t_max: transcript time range. parts: list of (start, end) per prompt part, to catch a
    missing part. Messages are written for the AI that produced the answer.
    """
    errors = []
    if not items:
        return ["The answer contains no snippets."]
    answers = []
    good = []
    for i, it in enumerate(items):
        lab = _label(i, it)
        if not isinstance(it, dict):
            errors.append("%s is not an object." % lab)
            continue
        missing = [k for k in ("concept", "start", "end", "summary", "questions") if k not in it]
        if missing:
            errors.append("%s is missing: %s." % (lab, ", ".join(missing)))
            continue
        if isinstance(it["start"], bool) or isinstance(it["end"], bool):
            errors.append("%s: start and end must be numbers of seconds, e.g. 268.7." % lab)
            continue
        try:
            start, end = float(it["start"]), float(it["end"])
        except (TypeError, ValueError):
            errors.append("%s: start and end must be numbers of seconds, e.g. 268.7." % lab)
            continue
        if start != start or end != end or abs(start) == float("inf") or abs(end) == float("inf"):
            errors.append("%s: start and end must be finite numbers." % lab)
            continue
        if (not isinstance(it["concept"], str) or not it["concept"].strip()
                or not isinstance(it["summary"], str) or not it["summary"].strip()):
            errors.append("%s: concept and summary must not be empty." % lab)
        if end <= start:
            errors.append("%s: end (%s) must be after start (%s)." % (lab, it["end"], it["start"]))
            continue
        if start < t_min - 1 or end > t_max + 1:
            errors.append("%s: %s-%s is outside the transcript (%s-%s). Copy timestamps from the "
                          "transcript lines." % (lab, mmss(start), mmss(end), mmss(t_min), mmss(t_max)))
        dur = end - start
        if dur > max_s:
            errors.append("%s is %d seconds long (%s-%s); the maximum is %d. Split it into consecutive "
                          "parts." % (lab, dur, mmss(start), mmss(end), max_s))
        elif dur < min_s:
            errors.append("%s is only %d seconds long; the minimum is %d. Merge it into the neighbouring "
                          "snippet it belongs to." % (lab, dur, min_s))
        qs = it["questions"]
        if not isinstance(qs, list) or len(qs) != 2:
            errors.append("%s must have exactly 2 questions (has %s)." % (
                lab, len(qs) if isinstance(qs, list) else "none"))
            qs = qs if isinstance(qs, list) else []
        for qi, q in enumerate(qs):
            ql = "%s, question %d" % (lab, qi + 1)
            if not isinstance(q, dict):
                errors.append("%s is not an object." % ql)
                continue
            for k in ("q", "options", "answer", "explanation"):
                if k not in q:
                    errors.append("%s is missing \"%s\"." % (ql, k))
            if "q" in q and (not isinstance(q["q"], str) or not q["q"].strip()):
                errors.append("%s: \"q\" must be a non-empty string." % ql)
            if "explanation" in q and (not isinstance(q["explanation"], str) or not q["explanation"].strip()):
                errors.append("%s: \"explanation\" must be a non-empty string." % ql)
            opts = q.get("options")
            if "options" in q and (not isinstance(opts, list) or len(opts) != 4
                                   or not all(isinstance(o, str) and o.strip() for o in opts)):
                errors.append("%s must have exactly 4 non-empty text options." % ql)
            elif isinstance(opts, list) and len(set(o.strip() for o in opts)) != 4:
                errors.append("%s has duplicate options." % ql)
            a = q.get("answer")
            if "answer" in q:
                if isinstance(a, bool) or not isinstance(a, int) or not 0 <= a <= 3:
                    errors.append("%s: answer must be the 0-based index of the right option (0, 1, 2 or 3), "
                                  "got %s." % (ql, json.dumps(a)))
                else:
                    answers.append(a)
        good.append((start, end, lab))
    good.sort()
    cut_points = [ps for ps, _ in (parts or [])[1:]]
    for (s1, e1, l1), (s2, e2, l2) in zip(good, good[1:]):
        # A part's last snippet may run into the next part's first lines (lookahead); that small
        # overlap is expected and trimmed in post-processing.
        crosses = any(s1 < c - 0.5 and s2 >= c - 0.5 for c in cut_points)
        if s2 < e1 - 0.5 and not crosses:
            errors.append("%s (%s-%s) overlaps %s (%s-%s). Snippets must not overlap." % (
                l2, mmss(s2), mmss(e2), l1, mmss(s1), mmss(e1)))
    if len(answers) >= 8:
        top = max(range(4), key=answers.count)
        if answers.count(top) > len(answers) / 2:
            errors.append("The correct answer is option %d in %d of %d questions, so students could guess it. "
                          "Reorder the options so the correct one is spread evenly over 0-3 (update \"answer\" "
                          "to match)." % (top, answers.count(top), len(answers)))
    if parts and len(parts) > 1:
        for n, (ps, pe) in enumerate(parts, 1):
            # Only parts of 5+ minutes: a short part may legitimately be all admin talk.
            if pe - ps >= 300 and not any(ps - 1 <= s < pe for s, _, _ in good):
                errors.append("No snippets for part %d (%s-%s) were pasted. Send your JSON for part %d again; "
                              "if you haven't received part %d yet, the user will paste it next."
                              % (n, mmss(ps), mmss(pe), n, n))
    if len(errors) > max_errors:
        errors = errors[:max_errors] + ["... and %d more problems like these." % (len(errors) - max_errors)]
    return errors


def fix_message_for(errors, parts_count=1):
    if not errors:
        return ""
    lines = "\n".join("- " + e for e in errors)
    if parts_count > 1:
        ask = ("Fix them and send the corrected JSON again for each part that had problems: the complete array "
               "for that part, one ```json code block per part, with no other text.")
    else:
        ask = ("Fix them and send the complete corrected JSON array (all snippets, not only the changed ones) "
               "inside one ```json code block, with no other text.")
    return "Your answer had these problems:\n%s\n\n%s" % (lines, ask)


# ---------------------------------------------------------------- building the package


def _segments(lecture_id: str) -> list[dict]:
    return transcript_io.validate_segments(lectures.load_transcript(lecture_id))


def _lecture_line(meta: dict) -> str:
    bits = [meta.get("title") or meta.get("lecture_id", "")]
    extra = ", ".join(x for x in (meta.get("course") or "",
                                  "lecture %s" % meta["lecture_number"] if meta.get("lecture_number") else "") if x)
    return bits[0] + (" (%s)" % extra if extra else "")


def _instructions() -> str:
    assert _JSON_RULE_API in snippet_maker.SNIPPET_PROMPT, "SNIPPET_PROMPT changed; update _JSON_RULE_API"
    return snippet_maker.SNIPPET_PROMPT.replace(_JSON_RULE_API, _JSON_RULE_CHAT)


def _part_header(meta: dict, n: int, total: int, start: float, end: float) -> str:
    out = ["# Rewind snippet request: part %d of %d" % (n, total), "",
           "Lecture: %s" % _lecture_line(meta), "", "## Instructions", "", _instructions(), "",
           "## Output format", "",
           "One JSON array. Every element looks like this example (the example is from a different lecture; "
           "copy its shape, not its content):", "",
           "```json", json.dumps(EXAMPLE, indent=1, ensure_ascii=False), "```", ""]
    out += ["## This part", ""]
    if total == 1:
        out.append("The transcript below is the whole lecture.")
    else:
        out.append("The lecture is split into %d parts because it is long. This is part %d, covering %s-%s "
                   "(%s to %s seconds)." % (total, n, mmss(start), mmss(end), start, end))
        if n > 1:
            out.append("Continue from part %d: output only snippets for this slice, as a new JSON array. "
                       "Don't repeat snippets from earlier parts. Your first snippet starts at or after [%s]. "
                       "If the first lines only finish a topic that began in part %d, skip them and start at "
                       "the first new topic." % (n - 1, start, n - 1))
        else:
            out.append("Output only snippets for this slice. I will send part 2 after your reply.")
        if n < total:
            out.append("After the line marked LOOKAHEAD the transcript continues with the first lines of part %d. "
                       "Only use them to finish your last snippet's topic (its end may fall there); don't start "
                       "new snippets in them." % (n + 1))
    out += ["Use the absolute timestamps shown in the transcript.", "",
            "## Transcript (part %d of %d)" % (n, total), ""]
    return "\n".join(out) + "\n"


def split_even(segs: list[dict], max_chars: int, max_seconds: float) -> list[list[dict]]:
    """Split into the fewest parts of roughly equal duration so each stays under both limits.

    No overlap: each segment is in exactly one part, so answers can simply be concatenated.
    """
    sizes = [len(snippet_maker.format_transcript([s])) + 1 for s in segs]
    t0, t1 = segs[0]["start"], segs[-1]["end"]
    n = max(1, -(-sum(sizes) // max_chars), int(-(-(t1 - t0) // max_seconds)))
    def cut_index(target: float) -> int:
        # Nearest line within 90 s of the target that follows a finished sentence; else the nearest line.
        nearest = min(range(1, len(segs)), key=lambda i: abs(segs[i]["start"] - target))
        clean = [i for i in range(1, len(segs)) if abs(segs[i]["start"] - target) <= 90
                 and re.search(r"[.?!][\"')\]]?\s*$", segs[i - 1]["text"])]
        return min(clean, key=lambda i: abs(segs[i]["start"] - target)) if clean else nearest

    while True:
        if len(segs) < 2:
            return [segs]
        cuts = sorted({cut_index(t0 + (t1 - t0) * k / n) for k in range(1, n)})
        edges = [0] + cuts + [len(segs)]
        parts = [segs[a:b] for a, b in zip(edges, edges[1:]) if b > a]
        too_big = any(sum(len(snippet_maker.format_transcript([s])) + 1 for s in p) > max_chars for p in parts)
        if not too_big or n >= len(segs):
            return parts
        n += 1


def _parts(lecture_id: str) -> tuple[dict, list[list[dict]], list[str]]:
    meta = lectures.load_meta(lecture_id)
    segs = _segments(lecture_id)
    if not segs:
        raise ValueError("Transcript is empty.")
    # Header size barely depends on the part number; reserve room for it, then split the transcript.
    header_len = len(_part_header(meta, 99, 99, segs[-1]["end"], segs[-1]["end"]))
    budget = max(5_000, PART_MAX_CHARS - header_len - 4_000)  # 4k: room for the lookahead lines
    windows = split_even(segs, budget, PART_MAX_SECONDS)
    texts = []
    for n, w in enumerate(windows, 1):
        text = _part_header(meta, n, len(windows), w[0]["start"], w[-1]["end"]) + snippet_maker.format_transcript(w)
        if n < len(windows):
            nxt = windows[n]
            ahead = [s for s in nxt if s["start"] < nxt[0]["start"] + LOOKAHEAD_SECONDS]
            text += ("\n\n----- LOOKAHEAD (start of part %d): only to finish your last snippet, "
                     "no new snippets here -----\n" % (n + 1)) + snippet_maker.format_transcript(ahead)
        texts.append(text + "\n")
    return meta, windows, texts


def part_ranges(lecture_id: str) -> list[tuple[float, float]]:
    _, windows, _ = _parts(lecture_id)
    return [(w[0]["start"], w[-1]["end"]) for w in windows]


def build_chat_prompt(lecture_id: str, part: int | None = None) -> list[str]:
    """Paste-ready messages, one per part. `part` (1-based) returns just that part."""
    _, _, texts = _parts(lecture_id)
    if part is None:
        return texts
    if not 1 <= part <= len(texts):
        raise ValueError("part must be between 1 and %d" % len(texts))
    return [texts[part - 1]]


def _start_here(meta: dict, total: int) -> str:
    files = ("PROMPT-part-1.md (the message to paste)" if total == 1
             else "PROMPT-part-1.md ... PROMPT-part-%d.md (the messages to paste, in order)" % total)
    more = ("" if total == 1 else
            "3. When it has answered, paste PROMPT-part-2.md into the same chat%s. Keep every reply.\n"
            % ("" if total == 2 else ", and so on until part %d" % total))
    step = 3 if total == 1 else 4
    return f"""# Make snippets for "{meta.get('title', '')}" with any AI chat

You need: this folder and an AI chat (ChatGPT, Gemini or Claude). No API key, no installation.

1. Open the AI chat in your browser and start a new conversation. Choose its strongest model if you can.
2. Open PROMPT-part-1.md with any text editor (Notepad or TextEdit is fine), copy everything, paste it into the chat and send it.
{more}{step}. Copy the AI's {"reply" if total == 1 else "replies (all of them, one after the other)"} into a text file, or keep {"it" if total == 1 else "them"} ready to paste. Text around the JSON is fine.
{step + 1}. In Rewind open AI settings, find this lecture, paste or upload the answer and press Import.
   If Rewind lists problems, press "Copy fix message for your AI" and paste it into the same chat.
   Then import again with all the replies, old and new: Rewind uses the newest answer for each part.

If the reply stops in the middle, write in the chat: "Your answer was cut off. Send the complete JSON again in one block."

Files: {files}, transcript.json, meta.json, schema.json.

Using a coding agent (Claude Code, Codex, Gemini CLI)? Open AGENT.md instead.
"""


def _agent_md(lecture_id: str, meta: dict, ranges: list) -> str:
    return f"""# Task for a coding agent: make Rewind snippets for lecture `{lecture_id}`

Lecture: {_lecture_line(meta)}

Work in this folder. Do the whole task without asking questions.

1. Read `transcript.json` (array of {{"start", "end", "text"}}, seconds) and the instructions below.
   You may read it in slices; the PROMPT-part-N.md files show the same transcript as "[123.4] text" lines.
2. Write `raw_llm_output.json`: ONE JSON array of snippets for the whole lecture, all parts merged
   in time order. Format: `schema.json`; an example element is in PROMPT-part-1.md.
3. Validate: `python check.py raw_llm_output.json` (standard library only). Fix every reported problem
   and run it again until it prints OK.
4. Import:
   - inside the Rewind repository: `python -m rewind.context_package import {lecture_id} <path to raw_llm_output.json>`
     (fix any reported problems and run it again until it succeeds);
   - otherwise tell the user to upload raw_llm_output.json in Rewind's AI settings for this lecture.

Transcript range: {mmss(ranges[0][0])}-{mmss(ranges[-1][1])}.

## Instructions

{snippet_maker.SNIPPET_PROMPT.replace(_JSON_RULE_API, "Write JSON only: one array, no prose.")}
"""


_CHECK_PY_HEAD = '''"""Check raw_llm_output.json for Rewind. Usage: python check.py raw_llm_output.json

Standard library only. Prints OK, or the problems to fix.
"""
import json
import re
import sys
'''

_CHECK_PY_MAIN = '''

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "raw_llm_output.json"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    items, errors = extract_snippets(text)
    if not errors:
        errors = check_answer(items, T_MIN, T_MAX, PARTS, MIN_S, MAX_S)
    if errors:
        print("Problems (%d):" % len(errors))
        for e in errors:
            print("- " + e)
        sys.exit(1)
    print("OK: %d snippets" % len(items))


if __name__ == "__main__":
    main()
'''


def _check_py(ranges: list) -> str:
    consts = "\nT_MIN = %r\nT_MAX = %r\nPARTS = %r\nMIN_S = %r\nMAX_S = %r\n" % (
        ranges[0][0], ranges[-1][1], [tuple(r) for r in ranges], SECONDS_MIN, SECONDS_MAX)
    funcs = "\n\n".join(inspect.getsource(f) for f in (mmss, extract_snippets, _label, check_answer))
    return _CHECK_PY_HEAD + consts + "\n\n" + funcs + _CHECK_PY_MAIN


def build_package(lecture_id: str) -> bytes:
    """Zip (bytes) for one lecture. See module docstring for the contents."""
    meta, windows, texts = _parts(lecture_id)
    ranges = [(w[0]["start"], w[-1]["end"]) for w in windows]
    segs = [s for w in windows for s in w]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("START-HERE.md", _start_here(meta, len(texts)))
        for n, text in enumerate(texts, 1):
            z.writestr("PROMPT-part-%d.md" % n, text)
        z.writestr("AGENT.md", _agent_md(lecture_id, meta, ranges))
        z.writestr("transcript.json", json.dumps(segs, ensure_ascii=False, indent=1))
        z.writestr("schema.json", json.dumps(SCHEMA, indent=2))
        z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        z.writestr("check.py", _check_py(ranges))
    return buf.getvalue()


# ---------------------------------------------------------------- importing


def import_answer(lecture_id: str, raw_text: str) -> dict:
    """Validate an AI answer and, if it's fine, post-process and save it. Never raises on bad input."""
    def fail(errors, parts_count=1):
        return {"ok": False, "snippets_count": 0, "errors": errors,
                "fix_message": fix_message_for(errors, parts_count)}

    try:
        ranges = part_ranges(lecture_id)
    except ValueError as err:
        return fail([str(err)])

    def fail_r(errors):
        return fail(errors, len(ranges))

    if not raw_text or not raw_text.strip():
        return fail_r(["The answer is empty. Paste the AI's reply (the JSON array)."])
    segs = _segments(lecture_id)
    items, errors = extract_snippets(raw_text, ignore=EXAMPLE)
    if errors:
        return fail_r(errors)
    # Drop exact repeats (e.g. one part pasted twice in a single block).
    seen, unique = set(), []
    for it in items:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda it: (float(it["start"]) if isinstance(it, dict) and not isinstance(it.get("start"), bool) and isinstance(it.get("start"), (int, float)) else 0))
    errors = check_answer(unique, segs[0]["start"], segs[-1]["end"], ranges, SECONDS_MIN, SECONDS_MAX)
    if errors:
        return fail_r(errors)
    snippets = snippet_maker.finalize(unique, segs, lecture_id)
    if not snippets:
        return fail_r(["None of the snippets could be used after checking times against the transcript."])
    lectures.save_snippets(lecture_id, snippets)
    raw_path = lectures.lecture_dir(lecture_id) / "raw_llm_output.json"
    raw_path.write_text(json.dumps(unique, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "snippets_count": len(snippets), "errors": [], "fix_message": ""}


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] not in ("build", "prompt", "import"):
        print(__doc__.split("CLI:")[1].split("The answer")[0].strip())
        return 2
    cmd, lecture_id = argv[0], argv[1]
    if cmd == "build":
        out = argv[2] if len(argv) > 2 else "%s-package.zip" % lecture_id
        with open(out, "wb") as f:
            f.write(build_package(lecture_id))
        print("Wrote %s (%d parts)" % (out, len(build_chat_prompt(lecture_id))))
        return 0
    if cmd == "prompt":
        parts = build_chat_prompt(lecture_id, int(argv[2]) if len(argv) > 2 else None)
        sys.stdout.write("\n\n=====\n\n".join(parts))
        return 0
    if len(argv) < 3:
        print("usage: python -m rewind.context_package import <lecture_id> <answer file>")
        return 2
    with open(argv[2], encoding="utf-8") as f:
        result = import_answer(lecture_id, f.read())
    if result["ok"]:
        print("OK: imported %d snippets into %s" % (result["snippets_count"], lecture_id))
        return 0
    print(result["fix_message"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
