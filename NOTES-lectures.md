# NOTES-lectures

## Round 6 (T4) — grounded chat, snippet quality, and evaluation

Work stayed strictly within T4-owned files (`rewind/chat.py`, `rewind/snippet_maker.py`, `rewind/context_package.py`, `tests/test_chat.py`, `tests/test_snippet_maker.py`, `tests/test_context_package.py`, `tests/chat_eval.json`, `tools/eval_chat.py`, `NOTES-lectures.md`). No real lecture JSON/assets, `.env`, settings files, or databases were modified; no live AI calls or Git commands were run.

### Completed work

1. **Grounded chat audit and fixes (`rewind/chat.py`)**
   - **Stream & `llm.chat` integration:** `_stream_llm` calls `llm.chat(system, messages, max_tokens=800, stream=True, purpose="runtime")` (passing `purpose="runtime"` when supported). `run_turn` closes `chunks` in a `finally` block, and `respond` closes the inner `run_turn` generator in `finally`, so early cancellation or mid-stream provider errors deterministically release provider resources. Empty / meta-only streams raise `RuntimeError` rather than emitting an empty completed turn.
   - **Citation filtering (`CitationFilter`):** fixed a prefix-matching bug where `_LOOKS_LIKE_CITE` flushed `"[S"` early before `"[Slide N]"` could complete, which previously let invalid `[Slide 99]` citations leak and prevented valid `[Slide N]` citations from being recorded. Added `_could_be_cite()` prefix matching, nested `[` flushing, `< 60` seconds/minutes range validation, partial trailing `<<META` prefix stripping (`finish()`), non-greedy JSON object parsing via `JSONDecoder.raw_decode`, and Italian outside-material fallback phrases. Note: `CitationFilter` validates lecture time bounds and slide existence only; it is **not** proof that a claim is semantically supported.
   - **Context, budget & prompt injection hardening:** `build_materials` now partitions segments (`before`, `clip`, `after`) before `chunk_segments()` so lines immediately before `snippet["start"]` do not merge across the clip boundary and drop the clip's opening lines. Accounts for fixed block overhead within `budget`. Sanitizes embedded `<<META` in transcript/slide text, and `CHAT_PROMPT` explicitly instructs the model to treat all transcript/slide text strictly as data (never as instructions) and only cite lines/slides that support the claim.
   - **`load_slides` & history validation:** when `rewind.slides.load_slides` exists, `ValueError` on malformed/unsafe manifests logs a warning and returns `[]` instead of falling back to unvalidated raw JSON reads. `_clean_history` rejects non-list inputs, skips non-dict items or non-string content, and raises `ValueError` if the trailing `user` message is empty/whitespace.

2. **Snippet verification, quality & context-package validation (`rewind/snippet_maker.py`, `rewind/context_package.py`)**
   - `verify_and_repair()` now sets `"verified": bool` (`True` only when `questions > 0` and `unchecked == 0`) and logs a warning if any questions remain unchecked. `format_verification()` explicitly reports `"Verification incomplete (X of Y unchecked; NOT fully verified)"` when `unchecked > 0`, and lists snippet IDs with zero questions left.
   - `quality_warnings()` now flags snippets whose `"questions"` list is present and empty (`no questions (quiz unavailable for this clip)`).
   - `context_package.check_answer()` (kept stdlib-only for standalone `check.py`) now rejects `bool`/`NaN`/`inf` times, requires non-empty `str` for `concept`/`summary`/`q`/`explanation`, and `import_answer()` handles empty transcripts cleanly without raising.

3. **Deterministic tests & 15-item evaluation (`tests/test_chat.py`, `tests/chat_eval.json`, `tools/eval_chat.py`)**
   - `tests/test_chat.py`: **27 passed** using injected fake streams and temporary lecture/DB fixtures (clip-first budget, boundary partitioning, selected-lecture isolation, no-slides and malformed-slides handling, instruction-like text in materials, empty/malformed history, char-by-char citation/META filtering, strict vs labelled policy + gap flagging, Italian language, homework hints, mid-stream failure, early stream close, `llm.chat` signature, and offline 15-item eval).
   - `tests/chat_eval.json` + `tools/eval_chat.py`: 15 items across `econ01` and `psych02` (3 clarifications incl. 1 Italian, 3 why questions, 2 example requests, 3 outside-material questions across `strict` and `labelled` policies, 2 homework-style requests, 2 `slide_only` questions using in-memory `fixture_slides`). Default offline mode checks structural/prompt/policy/citation contracts only (`15/15` pass); `--live --max-calls N` is opt-in and bounded, and was **not** executed.

### Exact public functions and return shapes (T4 modules)

- `rewind.chat`:
  - `respond(student_id: str, snippet_id: str, messages: list[dict], stream_fn=None) -> Iterator[dict]`: yields `{"type": "text", "text": str}` chunks then `{"type": "done", "citations": [{"kind": "time", "lecture_id": str, "t": int} | {"kind": "slide", "lecture_id": str, "n": int}], "outside_materials": bool, "homework": bool}`. Flags gaps via `store.flag_gap` when `outside_materials` is True. Propagates stream errors without yielding `done`.
  - `build_prompt(snippet_id: str, messages: list[dict], policy: str | None = None) -> tuple[str, list[dict], dict]`
  - `run_turn(chunks: Iterable[str], context: dict) -> Iterator[dict]` (same events, plus optional `"dropped_citations"` in `done` which `respond` pops).
  - `build_materials(snippet: dict, segments: list[dict], slides: list[dict], budget: int) -> str`, `load_slides(lecture_id: str) -> list[dict]`, `chunk_segments(segments: list[dict]) -> list[dict]`, `fmt_time(seconds: float) -> str`, `system_prompt(policy: str) -> str`, `CitationFilter`.
- `rewind.snippet_maker`:
  - `make_snippets(transcript, lecture_id, title=None, ask=None, verify=True, stats=None) -> list[dict]`
  - `verify_and_repair(snippets, ask=None) -> dict`: `{"questions": int, "agreed": int, "unchecked": int, "verified": bool, "regenerated": int, "fixed": int, "dropped": int, "no_questions": list[str], "failures": list[dict]}`.
  - `format_verification(stats: dict) -> str`, `quality_warnings(snippets: list[dict]) -> list[str]`, `balance_answers(snippets: list[dict]) -> list[dict]`, `finalize(raw_snippets, segments, lecture_id) -> list[dict]`, `generation_requests(segments, title=None) -> list[dict]`, `request_raw_snippets(segments, title=None, ask=None) -> list[dict]`, `export_prompt(...)`, `import_raw(...)`.
- `rewind.context_package`:
  - `build_package(lecture_id: str) -> bytes`, `build_chat_prompt(lecture_id: str, part: int | None = None) -> list[str]`, `part_ranges(lecture_id: str) -> list[tuple[float, float]]`, `import_answer(lecture_id: str, raw_text: str) -> {"ok": bool, "snippets_count": int, "errors": list[str], "fix_message": str}`.

### Tests and checks run (after final edit)

- `..\cmo-venv\Scripts\python.exe -m pytest -q tests/test_chat.py`: **27 passed**
- `..\cmo-venv\Scripts\python.exe -m pytest -q tests/test_snippet_maker.py`: **66 passed**
- `..\cmo-venv\Scripts\python.exe -m pytest -q tests/test_context_package.py`: **31 passed**
- Combined T4 tests (`tests/test_chat.py tests/test_snippet_maker.py tests/test_context_package.py`): **124 passed** in 0.84 s.
- `..\cmo-venv\Scripts\python.exe tools\eval_chat.py`: **15/15 structural checks passed** (offline fixture mode; does **not** verify live LLM groundedness or model quality).

### Quality concerns recorded (existing read-only lectures, untouched)

- `demo01`: `demo01-003` summary starts with framing (`"The professor always thinks of a system "`); fixture only (`"hidden": true`).
- `econ01` (19 snippets): 0 `quality_warnings`, answer distribution `A5/B12/C10/D11` (predates `balance_answers`), no `slides` alignment yet, no verification run or professor approval state.
- `psych02` (37 snippets): 1 warning (`psych02-012` is `121 s`, 1 s over 120 s soft target), answer distribution `A18/B16/C27/D13` (predates `balance_answers`), no `slides` alignment yet, no verification run or professor approval state.
- `econ01-chat`: contains `meta.json` and `transcript.json` only (no `snippets.json` imported).

### Remaining blockers & handoffs

- **Not verified in Round 6:** live LLM groundedness/claim-support quality in `chat.respond` or `eval_chat.py --live`, and real PDF/PPTX slide alignment on `econ01`/`psych02` (both disallowed/deferred by Round 6 read-only rules).
- **T3 (`api.py` / `store.py`):** `chat.respond` now closes the provider stream on early caller close/interruption and expects `llm.chat(..., stream=True, purpose="runtime")` to raise on stream failure. Please ensure `/api/chat` closes the `respond(...)` generator on client disconnect so cleanup propagates. Also `snippet_maker.verify_and_repair` sets `stats["verified"]` and snippets can end up with `questions: []` if all fail verification (already tolerated by `public_snippet`, `api.py /api/answer` returns 400).
- **T1 (`web/`):** snippets with `questions: []` (if verification dropped all questions) should show an honest "Quiz unavailable for this clip" state rather than a broken empty quiz, and generated/verified content must not be presented as professor-approved.
- **Coordinator:** run combined suite across all terminals, review real local integration + bounded `--live` chat eval when authorized, and plan professor approval state/enforcement as the next coordinated task.

---

# Historical Round 3 notes (not current authorization)

Real lecture data for the demo, built without an API key. I wrote each lecture's raw LLM answer by hand,
following SNIPPET_PROMPT strictly, then ran it through the normal post-processing.

Python: `..\cmo-venv\Scripts\python.exe`. `tests/test_snippet_maker.py`: **51 passed**.

## Lectures

| id | lecture | video | length | snippets |
|---|---|---|---|---|
| econ01 | MIT 14.01 Principles of Microeconomics (Fall 2018), Lecture 1 "Introduction and Supply & Demand", Jonathan Gruber | https://www.youtube.com/watch?v=_OkTw766oCs | 34.6 min | 19 (53–119 s), 27:50 covered |
| psych02 | Yale PSYC 110 Introduction to Psychology, Lecture 2 "Foundations: This Is Your Brain", Paul Bloom | https://www.youtube.com/watch?v=vg01Q1BI4WM | 53.3 min | 37 (47–121 s), 51:58 covered |

Each folder holds `meta.json`, `transcript.json` (YouTube captions, en), `raw_llm_output.json` (the hand-written
model answer) and `snippets.json`. Skipped as admin: econ01's intro on course details and teaching style
(0:00–3:25) and its closing course roadmap and logistics (31:40–end).
Rejected sources: Bloom Lecture 1 (mostly course overview) and Walter Lewin's 8.01 (MIT withdrew his materials).

## What I changed in rewind/snippet_maker.py

1. **`--export-prompt FILE`**: fetches or loads the transcript, saves transcript.json and meta.json, and writes
   the exact system prompt and user message to FILE (`FILE.partN.ext` per window if split). No API call.
   It shares `build_user_messages()` with the real API path, so the export is byte-identical to what the model gets.
2. **`--import-raw FILE`**: parses raw model JSON (fences or prose tolerated, same as `ask_json`) and runs the
   normal `finalize()`. Without `--youtube`/`--transcript` it uses the lecture's saved transcript.json.
3. **SNIPPET_PROMPT**, from reviewing the output:
   - **Sentence-aligned cuts.** YouTube caption lines often split sentences: Bloom's lines look like
     "…our immaterial minds. Now, Descartes made two". My first pass had 13 psych02 snippets starting
     mid-sentence (e.g. "arguments for dualism…", "neurons. There are a lot…"). The new rule is to start on the
     line where the topic's first sentence begins, even if it carries the previous sentence's last words, and to
     end after the line that completes the last sentence.
   - **Skip agendas and roadmaps.** Lists of topics for this or later lectures, and talk about teaching style,
     are now named explicitly as admin. Gruber's closing roadmap and his teaching-style intro were borderline
     under the old wording.
   - **Answer positions.** The rule was only "vary its position". My first pass put the right answer at B in 63
     of 74 psych02 questions and 21 of 38 econ01 questions, so a student could pass by always picking B, and
     models have the same bias. The new rule asks for an even spread over 0–3 that is not mostly one position.
4. **Post-processing:** `warn_answer_skew()` in `finalize()` logs a warning when one position holds more than
   50% of the answers (8 questions minimum). It has a test.

Fixes to my own raw JSON, not caused by the prompt rules:
- Two recall questions (axis labels on the rose graph; how kidneys are allocated today) became application questions.
- psych02-013 ran 135 s; I ended it at 19:06.
- I shuffled the options with a fixed seed. Answers are now econ01 A5/B12/C10/D11 and psych02 A18/B16/C27/D13.

Review checklist after the fixes. Every snippet:
- starts on a sentence start and is 45–121 s
- has a concept phrased as a student would search it
- has a 2-sentence summary using only the professor's content
- has 2 application questions with one clearly right answer

Remaining nits:
- Some ends still drop the last few words of a sentence when the next topic starts on the same caption line.
  This is inherent in caption-level timestamps.
- psych02-012 is 121 s, 1 s over the soft limit.

## Demo questions (6 per lecture) and search check

Run without a key, so this is similarity only (`search.search`, top 3). `*` marks an expected snippet.

| lecture | type | question | top 1 (score) | hit |
|---|---|---|---|---|
| econ01 | own words | Why is water so cheap when diamonds cost a fortune? | econ01-007 Water-diamond paradox (0.675) | @1 |
| econ01 | own words | What does it mean that every choice has a hidden cost? | econ01-002 Opportunity cost (0.517) | @1 |
| econ01 | own words | How do you find the price where buyers and sellers agree? | econ01-009 Supply curve and market equilibrium (0.398) | @1 |
| econ01 | own words | What's the difference between asking what happens and what should happen in economics? | econ01-002 (0.542); 017, 004 | **miss** (expected 010 or 011) |
| econ01 | not covered | What is price elasticity of demand? | econ01-008 Demand curve (0.455) | above threshold |
| econ01 | homework | Solve: demand is Q = 1000 - 100P and supply is Q = 200P. Find the equilibrium price and quantity. | econ01-009 (0.518) | @1 |
| psych02 | own words | Is the mind just the brain, or do we have a soul? | psych02-035 Hard problem (0.577); 034, 005 | **miss** (expected 010/001/002; 005, Descartes on soul vs. body, is arguably right) |
| psych02 | own words | How do neurons send messages to each other? | psych02-017 Synapses and neurotransmitters (0.698) | @1 |
| psych02 | own words | Why do drunk people lose their inhibitions? | psych02-019 Curare and alcohol (0.471) | @1 |
| psych02 | own words | What happens when someone can see fine but can't recognize faces? | psych02-030 Agnosia, prosopagnosia (0.576) | @1 |
| psych02 | not covered | How does long-term memory work? | psych02-018 (0.321) | below threshold |
| psych02 | homework | Exercise 3: explain how an agonist differs from an antagonist and give one drug example of each. | psych02-018 Agonists and antagonists (0.518) | @1 |

**hit@1 8/10, hit@3 8/10.** Both homework questions hit at rank 1, and both start with keywords ("Solve",
"Exercise 3") that `quiz.is_homework` catches without an LLM.

Scores: correct hits 0.398–0.698 (lowest: equilibrium, 0.398). Not covered: elasticity 0.455, memory 0.321.

**Suggested SIMILARITY_THRESHOLD: 0.38** (now 0.35). It rejects the memory question and keeps every correct
hit, but the margin is thin (0.398 is the lowest correct hit), and this is only 12 questions.
The elasticity question (0.455) can't be separated by similarity alone, because "demand" dominates the
embedding. That case needs the LLM check in `search.answer`. I didn't edit config.

Notes for search (T2):
- The two misses both involve abstract paraphrases: "what happens vs. what should happen" for
  positive/normative, and "mind vs. soul" for dualism.
- The embedding text is concept + summary + the full transcript. The long transcript may dilute the concept.
  Worth testing concept + summary only, or a higher weight on the concept.

## Regenerating both lectures with a real key

Set in `.env`: `LLM_PROVIDER=anthropic`, `LLM_API_KEY=...`, `LLM_MODEL=<model id>`
(or the legacy `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`). Then:

```
..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --youtube https://www.youtube.com/watch?v=_OkTw766oCs --lecture-id econ01 --title "Introduction and Supply & Demand" --course "MIT 14.01 Principles of Microeconomics" --lecture-number 1 --languages en
..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --youtube https://www.youtube.com/watch?v=vg01Q1BI4WM --lecture-id psych02 --title "Foundations: This Is Your Brain" --course "Yale PSYC 110 Introduction to Psychology" --lecture-number 2 --languages en
..\cmo-venv\Scripts\python.exe -c "from rewind import search; search.index_all()"
```

These overwrite snippets.json. To compare against the hand-written version first, back up the folder.
Re-importing the hand-written answer without a key:
`..\cmo-venv\Scripts\python.exe -m rewind.snippet_maker --lecture-id econ01 --import-raw data/lectures/econ01/raw_llm_output.json`.
After a real run, check that the log has no "Correct answer is option X in N of M questions" warning.

Export the prompt only (no key needed): add `--export-prompt prompt.txt` to either command above.

---

# Round 4 (T4): the context package

`rewind/context_package.py` + `tests/test_context_package.py` (**29 tests**; with test_snippet_maker **80 passed**).
No changes to snippet_maker.py were needed this round: it reuses `SNIPPET_PROMPT`, `format_transcript`,
`finalize`, `MIN_SECONDS`/`MAX_SECONDS` as they are.

## Contract (as specified)
- `build_package(lecture_id) -> bytes` (zip)
- `build_chat_prompt(lecture_id, part=None) -> list[str]` (part is 1-based; `ValueError` if out of range)
- `import_answer(lecture_id, raw_text) -> {ok, snippets_count, errors, fix_message}`. It never raises on bad
  input. On ok it saves snippets.json and raw_llm_output.json; on failure it saves nothing.
- Extra: `part_ranges(lecture_id)` returns [(start, end)] seconds per part, useful for the UI's "part 1 of 3".
- CLI: `python -m rewind.context_package build|prompt|import <lecture_id> ...`

## Zip contents
START-HERE.md (5 steps for a chat AI, plus one line pointing coding agents to AGENT.md), PROMPT-part-N.md,
AGENT.md, transcript.json, schema.json (JSON Schema), meta.json, and **check.py**. check.py is the same
validation code as `import_answer`, shipped with the stdlib-only source via `inspect.getsource`, so a coding
agent can validate `raw_llm_output.json` outside the repo: `python check.py raw_llm_output.json`.

## Design decisions, from testing
- **Parts.** The limit is ≤60k characters and ≤30 minutes of lecture per part, split into equal halves or
  thirds, with each cut placed after a finished sentence (within 90 s of the even split). The 30-minute cap is
  about the *reply*: about 1.3k characters of JSON per snippet means a 53-minute lecture (37 snippets) would
  need a ~13k-token reply, which chat apps cut off. econ01 is 2 parts of 27k; psych02 is 2 parts.
- **Lookahead.** Each part except the last shows the first 2 minutes of the next part, marked LOOKAHEAD. The AI
  may finish its last topic there but not start new snippets. The next part is told to skip lines that only
  finish an earlier topic. Without this the econ01 cut fell inside the eBay example. Overlaps across a part
  boundary are allowed by the checks and trimmed by `finalize`.
- **The chat JSON rule.** SNIPPET_PROMPT says "no code fences" (right for the API). For chats that line is
  swapped for "one ```json block", and an assert guards against the prompt text drifting.
- **The example is from another subject** (Le Chatelier's principle). My first draft used "Opportunity cost",
  which is actually in econ01.
- **Newest answer wins.** Lecturers will paste whole chats: prompts, replies, fix messages and corrections. Each
  JSON block replaces earlier snippets that start inside its own time span, and blocks with separate spans are
  appended (parts or "continue" replies). A broken block followed by valid ones counts as superseded; a still
  missing part is then caught by the part check. The prompt's format example is ignored.
- **Fences** count only on their own line, and "```json" always opens a new block. A cut-off reply's
  unclosed fence otherwise swallowed the next reply.
- **Checks**, with messages addressed to the AI that made the answer:
  - missing fields; times not numbers, end ≤ start, or outside the transcript
  - snippets over 150 s ("split it") or under 20 s ("merge it")
  - not exactly 2 questions; not 4 distinct options; answer not 0–3
  - overlaps
  - the same answer position in more than 50% of questions (8 or more)
  - a part of 5+ minutes with no snippets ("No snippets for part 2 (17:19–34:38) were pasted…")
  The error list is capped at 25.
- **fix_message** lists the errors and asks for the complete corrected array. For multi-part lectures it asks
  only for the parts with problems, one block per part, which keeps the reply short so it isn't cut off again.

## End-to-end test with econ01 (acting as the chat AI, using only the PROMPT files)
I wrote two chat replies with prose around one ```json block each, read only PROMPT-part-1/2.md, and
imported them into a scratch copy of econ01, so the real folder was not touched.

| pasted | result |
|---|---|
| both replies | ok, 19 snippets, 53–121 s, answers A8/B13/C10/D7 |
| the whole chat (both prompts with their examples, plus both replies) | ok, 19 |
| part 2 cut off mid-JSON | error: "The JSON could not be read (… line 144 …). It may be cut off …" |
| only part 1 | error: "No snippets for part 2 (17:19-34:38) were pasted …" |
| cut-off reply, then the fix message, then part 2 resent | ok, 19 |

Quality matches round 3: same cuts, all application questions, no framing, sentence-aligned starts. Caveat:
I wrote both, so this shows the package is followable. It doesn't show how ChatGPT or Gemini would do.
**Worth trying for real with one free chat account before the demo.**

## Notes for T3 (api.py) and T1 (settings page)
- `/api/lectures/{id}/prompt` can return `{"parts": build_chat_prompt(id)}`. `part_ranges(id)` gives the times
  for labels like "part 1 of 2 (0:00–17:19)".
- After a failed import, the UI should tell the lecturer to paste the fix message into the same chat and then
  import **all** replies again (old and new); Rewind uses the newest answer per part. START-HERE.md says the same.
- On ok, reindex the lecture (snippets.json changed).

---

# Round 4 extra fix: econ01 regenerated with the real API (econ01-api)

`LLM_PROVIDER=openai_compatible` via OpenRouter, model `anthropic/claude-haiku-5.5`. One call, 90 s, same
transcript and SNIPPET_PROMPT (round 3 version):

```
python -m rewind.snippet_maker --transcript data/lectures/econ01/transcript.json --video-url https://www.youtube.com/watch?v=_OkTw766oCs --lecture-id econ01-api --title "Introduction and Supply & Demand" --course "MIT 14.01 Principles of Microeconomics" --lecture-number 1
```

| | hand-written econ01 | API econ01-api |
|---|---|---|
| snippets | 19, 27:50 covered | 22, 26:56 covered |
| outside 45–120 s | none (max 119 s) | 3: supply curve 42 s, "best for society" 43 s, kidney auction 125 s |
| starts mid-sentence | 0 | 2 ("sort of a third class of failures…", "which is basically, how freely…") |
| answer spread A/B/C/D | 5/12/10/11 | 13/17/10/**4** (no skew warning, since B is under 50%, but D is almost never right) |
| recall questions | 0 | 3 (how kidneys are allocated today; which level involves x/y graphs; which issue the surplus definition leaves open) |
| wrong answer key | 0 | **1**: "Why is the demand curve drawn downward sloping?" has none of the right reasons and marks "Because roses are a necessity" correct |
| implausible distractors | none | several ("The roads were too narrow", "A random number generator", "Because professors dislike mathematics") |
| summaries | no framing | most start "The professor says/describes…" (against the rule); econ01-api-016 crams three ideas into 2 sentences |
| concepts | broader ("Supply curve and market equilibrium") | finer and good for search ("Supply curve for roses" and "Market equilibrium" separate, "Defining 'best for society' as maximum surplus") |

**Kept the hand-written econ01; deleted `data/lectures/econ01-api`.** A copy of the API output is in my session
scratchpad only. The deleted folder had already been indexed (embeddings.npy existed), so restart or reindex a
running API server if it still lists econ01-api.

Takeaways:
- The cheap model follows the structure well (valid JSON, all fields, times snapped) but not the quality rules.
- One wrong answer key in 44 questions is the serious problem: a student who answers correctly is told
  they're wrong.
- Before relying on API generation for the demo, use a stronger model, or add a second "check each question's
  answer against the snippet" pass. That pass is a possible snippet_maker addition, not done (out of scope this
  round).
- Possible post-processing checks, cheap and in snippet_maker: flag summaries starting with "The professor"
  and snippets under 45 s.
