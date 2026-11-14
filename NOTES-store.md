# NOTES-store

## Round 6 T3 checkpoint (14 November 2026)

146 T3 tests passed with isolated settings/databases, offline mode and fake providers. Actual T2
`settings.save/public`, `llm.estimate(requests, purpose="preprocess")`, and actual T4 `chat.respond`
were exercised at the API boundary; provider output was mocked. No live AI calls or existing
lecture/database writes were made. Final results and the full contract are appended below.

- T1: `/api/settings` now accepts/returns `chat_policy` (`strict`/`labelled`) and
  `preprocess_model`; invalid values return 422. `/api/chat` emits an `error` event if the source
  ends without `done`; partial replies are never stored as completed assistant history. An explicit
  upstream `error` event also terminates the turn. Existing `text`/`done` event shapes remain intact.
- T1: text-only/missing-preview slides in `/api/ask.slides` retain `text` with `image_url: null`.
  Please render this readable fallback (the current `slides.js` filters out slides without images).
  Image-backed refs keep the existing compact shape. Public GET slides includes text for every page.
- T1: generation estimate has `scope` explaining that initial generation excludes verification,
  repair and retries. T2 supplies nullable cost for unknown prices; avoid displaying null as free.
- T2: API estimate now supplies `purpose="preprocess"` both per request and as the optional
  estimate keyword; ingest injects an ask wrapper that defaults generation to preprocess while
  allowing snippet verification to pass runtime explicitly. No TypeError retry of paid work.
- T2: API upload limit now matches slides' 50 MiB source limit. `python-multipart` is installed in
  the current environment but missing from requirements.txt: please append it (T2 owns dependencies).
- T4: the API closes `respond` on failure/cancellation. Please propagate generator cleanup from
  `respond` through `run_turn` to the provider stream; API tests cannot prove SDK cleanup inside
  those nested generators. Gap writes remain the chat brain's responsibility.

## Round 6 final T3 handoff

Completed: audited the existing store/history/statistics implementation; fixed successful-turn
persistence, SSE error/end handling and disconnect cleanup; integrated current T2 settings/purpose/
estimate contracts; bounded and closed multipart uploads; aligned upload limits with slide extraction;
retained text-only slide answers; improved safe source paths, CLI errors and secret masking.
No store schema migration or public store signature change was needed. Only T3-owned files changed.

Public interfaces and shapes:

- `create_app(fake=None, db_path=None, warmer=None) -> FastAPI` is unchanged. Tests inject DB paths.
- `POST /api/chat {student_id, snippet_id, messages:[{role,content}]}` returns SSE JSON `text`, then
  `done` with `{citations, outside_materials:bool, homework:bool}`. Failed turns emit `error` with
  `message`, without `done`. Unavailable chat stays HTTP 409; missing clips 404; invalid final turns
  400 and malformed bodies 422. Last ten messages are passed to the chat brain. Student messages are
  logged before provider iteration. Only a valid, nonempty `done` turn saves an assistant reply;
  failures, early EOF and disconnects leave no completed assistant message. Closing/cancelling the
  response closes the chat iterator. Follow-up counts measure student questions, including failed turns.
- `store.log_chat(student_id, snippet_id, role, content, outside_materials=False, db_path=None) -> int`;
  `chat_history(student_id, snippet_id, db_path=None) -> list[{role,content,outside_materials,created_at}]`.
  History is isolated by student, clip and DB; ordering uses timestamp then row ID.
  `lecturer_stats(lecture_id=None, db_path=None, include_hidden=False) -> list[dict]` retains per-clip
  `followups`; lecturer API totals retain `followups` and `slide_only_pct`.
- `SettingsBody` additionally accepts optional `chat_policy: strict|labelled` and `preprocess_model:str`.
  GET/POST settings returns the masked T2 public shape. Invalid saves return 422; old merge/empty-key
  behavior remains. Existing loopback guards remain on settings/generation/import/upload.
- `snippet_requests(id,title=None) -> list[{system,user,max_tokens}]` captures initial generation
  without an AI call. The estimate endpoint adds request-level `purpose:preprocess`, passes the same
  keyword when supported, returns T2's estimate unchanged plus `scope` explaining exclusions.
  It is approximate initial-generation pricing; verification, repairs and retries cost extra.
  Unknown pricing remains null. `ingest.preprocess_kwargs() -> dict` adapts signatures before calling;
  it never retries a provider call because of an arbitrary TypeError.
- POST lecture slides remains local multipart `{file}` -> `{count:int,aligned:bool,align_error?,
  reindex_error?}`. File cap is 50 MiB, transport cap adds 64 KiB for multipart overhead; oversized files
  return 413 before extraction/source save. Type/empty/form errors return 400, parse failures 422,
  missing multipart support 501. Files close on success/error. GET lecture slides returns records
  `{n,title,text,image,image_url}` (plus existing metadata); no slides returns `[]`.
- `/api/ask` retains `{status,homework,snippet,lecture,alternatives,slides}` and redacted quizzes.
  Slide refs have `{lecture_id,n,title,image_url,score,source}`; text-only refs also contain `text`.
  `slide_only` uses null snippet and no gap. Hidden/missing slide refs are omitted. Slide PNG/JPEG/WebP
  media stays within the lecture and rejects traversal/escaping links.
- `ingest.save_source(id,filename,data=None,src=None) -> Path` sanitizes the basename and rejects
  invalid IDs/escaping source paths. `ingest_slides(id,path) -> int`, `align_slides(id) -> bool` and
  `run(args,transcriber=None,ask=None,indexer=None) -> 0|2` retain their contracts. Optional `--slides`
  works alongside teammate `{start,end,text}` `--transcript`, bypassing transcription. Corrupt slides
  surface an actionable `IngestError`; injected mock providers remain supported.

Validation: **146 passed, 1 dependency warning, 4.65s** with
`C:\Users\User\Documents\GitHub\cmo-venv\Scripts\python.exe`, using only
`tests/test_api.py tests/test_store.py tests/test_ingest.py`. The warning is PyMuPDF's ByteString
deprecation on the installed Python 3.15 interpreter; source syntax remains Python 3.11 compatible.
The launch wrapper redirects `config.DATA_DIR`/`DB_PATH` to a TemporaryDirectory and sets dummy
`LLM_MODE=none`/model/key defaults before pytest. Normal filesystem access was needed because the
sandbox denied pytest's default temporary-directory setup. There were no live API/model calls.

Coverage includes success, first-token/provider failure (one attempt, no retry), malformed streams,
early EOF, actual ASGI disconnect cancellation, iterator close, malformed request bodies, clip/student
history separation, follow-up totals, real T2 settings/estimate integration, actual T4 chat with fake
LLM chunks, local-only boundaries, quiz redaction, file traversal, byte limits and missing images.
A generated three-page PDF exercised **real T2 extraction/alignment + API upload/GET/media**, with
embedding vectors mocked and reindex invocation recorded. Search decisions/provider SDKs were mocked;
this does not establish live retrieval/grounding quality or browser behavior. No lecture assets were
downloaded and no existing lecture JSON, live settings, `.env` or databases were modified.

Remaining actions: **T1** render text-only slide refs and expose initial-estimate exclusions;
**T2** append `python-multipart` to requirements (installed here but undeclared);
**T4** ensure closure propagates through nested chat/provider generators. **Coordinator** run the
combined suite and real local API/browser flow after all workers stop; review Python-version and
professor-approval publication work separately. There is no further T3 code blocker.

Process note: an initial read-only Git status/branch check ran before the no-Git round instruction
was read. No subsequent Git commands, commits, pushes, worktrees or process/agent launches occurred.
T3 stopped editing after this handoff.

Owned: `rewind/store.py`, `rewind/ingest.py`, `tests/test_store.py`, `tests/test_ingest.py`, `api.py`,
`tests/test_api.py`, this file. Older sections below describe historical rounds and are superseded by
the Round 6 handoff above.

## store.py

- `log_ask` reads `snippet_id` from `result["best"]["id"]`, matching what `search.answer()` returns. It still accepts the old `{"best": {"snippet": {...}, "score": ...}}` form.
- Added for the API, with existing signatures unchanged:
  - `ask_totals() -> {"asks", "found", "not_covered"}`
  - `lecturer_stats` rows now include `end`
  - `resolve_gap` returns `bool` (False for an unknown id) and resolves every unresolved row with the same lowercase wording
- Every function takes an optional `db_path=` (default `config.DB_PATH`).

## api.py

Run with `uvicorn api:app --reload --port 8000`. It prints `Rewind API mode: real|fake (reason)` at startup.

- **Fake mode** is used when `REWIND_FAKE=1` or `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL` are missing (or `rewind.search` fails to import). It uses `tests/fakes.py` for **search only**.
  - **Store in fake mode:** it uses the real SQLite store on `data/rewind-fake.db`. The fake store in `tests/fakes.py` has no `lecturer_stats`, `open_gaps`, `resolve_gap` or `seed_demo_data`, so the lecturer endpoints couldn't work with it.
  - **Homework check in fake mode:** uses only quiz's keyword regexes, so it never calls the LLM.
  - **Integration:** please add `data/rewind-fake.db` to `.gitignore`.
- **Real mode:** errors from `search.answer` (API/config) return **503**. They are never logged as a gap.
- **`/api/ask`:** logs the ask. A `not_covered` result also calls `flag_gap`, so the browser shouldn't flag gaps itself.
- **`/api/answer`:**
  - Grades with `quiz.grade` on the full snippet, server-side, and logs the attempt.
  - Returns 404 for an unknown snippet and 400 for a bad `question_index` or `chosen`.
- **`/api/lecturer/gaps`:** `gap_id` is the first row of each group. Resolving it resolves the whole group. An unknown id returns 404.
- **`/api/graph`:**
  - Node ids are `course:<lower name>`, `lecture:<id>`, `snippet:<id>` and `concept:<lower, whitespace-collapsed concept>`.
  - The same concept in several lectures becomes one shared node.
- **`/media/{lecture_id}/{filename}`:**
  - Only simple names with media extensions (mp4, webm, m4v, mov, mkv, ogg/ogv, mp3, m4a, wav, vtt) are served, and only from inside `data/lectures/<id>/`. Anything else returns 404.
  - Range requests return 206 via Starlette 1.10 `FileResponse` (tested).
- **`web/`:** mounted at `/` with `html=True`, after the API routes.
- **CORS:** allows any port on localhost, 127.0.0.1 and [::1].

## Dependencies

Appended `fastapi`, `uvicorn` and `httpx` (httpx is needed by TestClient) to requirements.txt. The installed versions are FastAPI 0.162.7, Starlette 1.10.0 and uvicorn 0.54.0.

## Tests

`pytest tests/test_store.py tests/test_api.py`: 24 passed. The API tests run in fake mode on a temporary DB. They cover every endpoint, media range requests and path traversal, and include a test that no response contains `answer`, `explanation`, `text`, `correct_index` or full snippet text before `/api/answer`.

## For integration: .gitignore additions

```
data/rewind-fake.db
data/lectures/*/video.*
data/lectures/*/snippet_prompt.md
```

The current `.gitignore` covers `*.mp4`/`*.mkv`/`*.mov` but not `.webm` or `.m4v`. `video.*` covers everything ingest writes.

## rewind/ingest.py (round 3)

```
python -m rewind.ingest --video C:\path\lecture.mp4 --lecture-id ai01 --title "..." --course "..." --lecture-number 1 [--transcript t.json] [--raw raw.json] [--force]
```

1. **Video:** copies the video to `data/lectures/<id>/video.<ext>` (extension lowercased). It writes `meta.json` with `video_url: "media/<id>/video.<ext>"`, which `/media` serves with range requests. The copy is skipped if a same-size file is already there.
2. **Transcript:**
   - If `--transcript` is given, it's validated with `transcript_io.load_transcript` and saved.
   - Otherwise faster-whisper runs with model `small`, `device="auto"` (CUDA if available) and VAD on, then `transcript_io.from_whisper`. Progress prints in 10% steps.
   - If neither works, ingest exits 1 with instructions. **faster-whisper is not installed** in the venv and not in requirements.txt (optional). To install it: `pip install faster-whisper`.
3. **Snippets:**
   - **LLM:** used if `llm.available()` returns True (looked up with getattr). Until T2 adds it, the fallback is that `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` are both set. It calls `snippet_maker.make_snippets`.
   - **`--raw`:** takes priority over the LLM. It uses `snippet_maker.import_raw(raw_path, segments, lecture_id)` when that exists. **T4, please use that name and signature, or tell me yours.** Until then it uses `llm.parse_json` + `snippet_maker._as_list` + `snippet_maker.finalize`, which is the same validation.
   - **Neither:** it writes `data/lectures/<id>/snippet_prompt.md`, the exact prompt(s) `request_raw_snippets` would send (captured by passing a fake `ask`). It prints the rerun command with `--transcript` (no re-transcription) and `--raw`, then exits 2 without indexing.
4. **Index:** `search.index_lecture(<id>)`. Then it prints the duration, segment and snippet counts, and `http://localhost:8000/`.

- Refuses `--lecture-id` values that aren't simple names, and lecture folders that already have `snippets.json` unless `--force`.
- `tests/test_ingest.py` (10 tests) uses a tiny dummy .mp4 and a tmp lectures dir, with the transcriber, faster-whisper, LLM and indexer mocked.

## api.py changes (round 3)

- **Real vs fake mode:**
  - When `rewind.llm.available` exists, real mode no longer needs `ANTHROPIC_API_KEY`: search runs threshold-only. If `available()` is False, the homework check also skips the LLM. Startup prints `real (no LLM: threshold-only search, keyword homework check)`.
  - Before T2's change lands, a missing key still means fake mode.
- **Checked, with no .env:** `llm.available` doesn't exist yet in `rewind/llm.py`, so `import api` currently starts in **fake** mode (`ANTHROPIC_API_KEY is not set`). With `llm.available` mocked, `test_mode_real_without_key_once_llm_available_exists` confirms real mode. Once T2 lands, rerun `python -c "import api"` and expect `real`.
- `test_graph` now uses a tmp copy of demo01, since econ01 and psych02 now exist under data/lectures.
- `pytest tests/test_store.py tests/test_api.py tests/test_ingest.py`: 35 passed.

## Round 4 (T3): extra fixes + AI endpoints

### Round 3 extra fixes
- **Warm-up:**
  - At startup (FastAPI lifespan), a background thread runs `search.search("warm up", top_k=1)`. That loads the embedding model and every lecture index, building missing ones.
  - `/api/health` is now `{"ok", "mode", "status": "warming"|"ready"|"error"}`, plus `"error"` if warm-up failed. Questions still work while warming; they just load lazily. Fake mode is "ready" at once.
  - **T1:** `mode` is unchanged; the new field is `status`.
  - **Smoke test** (real server, port 8766, stopped by its PID): warming → ready in ~9 s. The first `/api/ask` after that took 2.7 s including the OpenRouter coverage check (found econ01-008). I deleted the one ask row this wrote to data/rewind.db (student_id "smoke").
- **Hidden lectures:**
  - `"hidden": true` in meta.json skips a lecture in `/api/lectures`, `/api/examples`, `/api/graph` and `/api/ask`.
  - In `/api/ask`, a hidden best match becomes not_covered (and is logged and flagged as a gap). Alternatives aren't promoted, because they didn't pass the coverage check. Hidden alternatives are dropped.
  - `/api/answer`, rewatch and `/media` still work for hidden lectures.
  - Set `"hidden": true` on `data/lectures/demo01/meta.json`.
  - **Not filtered (not in the brief):** `/api/lecturer/stats` still lists hidden lectures. `store.seed_demo_data` makes `demo01-002` the hard snippet when it exists, so the seeded lecturer demo will highlight the hidden fixture. **Suggest:** in store.py, seed only visible lectures and pick the hard snippet from them; in the API, also filter stats. I can do either if assigned.
- **Mode:** real mode now only needs `rewind.search` to import, because `llm.available()` exists. Without an LLM it prints `no LLM configured: threshold-only search, keyword homework check`. The homework check is now chosen per request (`llm.available()` at that moment), so changing AI settings takes effect without a restart.

### AI endpoints (contract in docs/round4-prompts.md)
- **Local only:** the `local_only` dependency returns 403 unless the TCP peer is 127.0.0.0/8, ::1 or IPv4-mapped loopback. `X-Forwarded-For` is ignored on purpose. Note that `localhost` as a *host string* (TestClient's "testclient" too) is not trusted.
  - Applied to: GET/POST `/api/settings`, POST `/api/settings/test`, `/api/lectures/{id}/package|prompt|import|generate|generate/status`.
  - `settings.html` is guarded by a `StaticFiles` subclass. It blocks any case or trailing-dot variant and anything resolving to the same file (`/SETTINGS.HTML`, `/js/../settings.html`, `settings.html::$DATA`).
- **`/api/ai-status`** (public): `{mode, provider, can_generate, coverage_check}` only.
  - **mode:** from `settings.public()` when it exists; else "api" if `llm.available()`, else "none".
  - **provider:** from settings, else `config.get_provider()`.
  - **coverage_check:** `llm.available()` and real mode.
- **Settings:**
  - POST `/api/settings` validates mode and provider (422 for bad values) and merges into `settings.load()`. An omitted or empty `api_key` keeps the current key, and there is currently no way to clear a key.
  - Every settings, test-connection and generate-error response passes through `scrub_secret`, which masks any string containing the raw key from settings or the `LLM_API_KEY`/`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` env vars, even if `settings.public()` leaked it. **T2:** `llm` caches clients per (provider, key); make sure `save()` takes effect without a restart.
- **501:** if `rewind.settings`, `rewind.context_package` or a function (`llm.test_connection`) is missing, the endpoint returns 501 with "… isn't available yet". At the time of writing, settings.py and context_package.py don't exist yet, so those endpoints currently return 501 on the real server.
- **Package, prompt and import:**
  - **Package:** returns `application/zip` as `rewind-<id>-package.zip`.
  - **Prompt:** returns `{parts}`.
  - **Import:** accepts `raw_text` (max 5M chars) and returns import_answer's result plus `reindexed: bool`. On a reindex failure it also returns `reindex_error`; snippets are saved anyway and search re-indexes lazily. Reindex is a no-op in fake mode.
- **Generate:**
  - POST starts a daemon thread and returns `{ok: true, snippets_count: null, error: null, state: "running"}`.
  - It refuses with `ok: false` + `error` when there's no LLM, no transcript.json, or a run is already going for that lecture.
  - The job calls `snippet_maker.make_snippets(transcript, id, title=meta.title)`, saves only on success (a failure keeps the old snippets), then reindexes.
  - Status: `{state: idle|running|done|error, message, snippets_count}`.

### For others
- **tests/test_store.py::test_lecturer_stats (not mine this round):** fails since econ01/psych02 exist. It compares `lecturer_stats("demo01")` with `lecturer_stats()` over all lectures. Fix: `assert store.lecturer_stats("demo01") == [r for r in stats if r["lecture_id"] == "demo01"]`, and pick `stats[0]` from that filtered list. This is unrelated to the hidden flag.
- **.gitignore** still needs `data/rewind-fake.db`, `data/lectures/*/video.*` and `data/lectures/*/snippet_prompt.md`. **T2:** also `data/settings.json`.
- **docs/round4-prompts.md:** the brief mentions an "AI COSTS" section, but the file has none. Generate calls the paid API once per transcript window; there is no cost estimate or confirmation in the API.

### Tests
`pytest tests/test_api.py`: 43 passed. They cover the local-only guard on every guarded endpoint (remote client, spoofed X-Forwarded-For), settings.html variants, masked key (including a leaky `public()`), settings merge, 501 paths, ai-status, package/prompt, import ok/error/reindex failure, generate (background, double start, error keeps snippets, key scrubbed from errors), warm-up warming/ready/error, and hidden lectures. API tests run on a tmp copy of demo01 with `hidden` removed.

## Round 4 follow-up (T3)

### 1. GET /api/lectures/{id}/generate/estimate (local only)
- Builds exactly the requests Generate would send, without calling the AI: it runs `snippet_maker.request_raw_snippets` with a recording `ask`, and `api.snippet_requests()` returns them.
- Calls **`llm.estimate(requests)`**, where `requests = [{"system", "user", "max_tokens"}, ...]` (one per transcript window), and returns its result unchanged (key-scrubbed).
- Errors: 501 if `llm.estimate` doesn't exist (it doesn't yet), 404 for an unknown lecture, 409 when there's no transcript.json.
- **T2, please match that signature, or tell me yours.** For econ01 it's currently 1 request: ~41k characters of user text, `max_tokens` 32000.

### 2. /api/examples
- At most one question per visible lecture (up to 3), using `api.example_question(concept, snippet_text)`.
- **Which concepts make a question:** skips concepts that are over 4 words or already questions, or contain punctuation, "and"/"or"/"vs", prepositions, Title Case headings, or a bare name.
- **Lower-casing:** lower-cases the first word unless it's an acronym (GDP, L-DOPA) or the lecture text capitalises it mid-sentence (a proper noun).
- **"the":** added unless the head noun is plural ("What are …"), a name, or uncountable (a small list plus -ism/-ics/-tion/… endings).
- **Choice per lecture:** plain concepts are preferred over names and titles.
- **Current output:** `["What is opportunity cost?", "What are neural networks?"]`. It's 2, not 3, because only econ01 and psych02 are visible.

### 3. Hidden lectures in lecturer stats + demo data (store.py, test_store.py)
- **`store.lecturer_stats(lecture_id=None, db_path=None, include_hidden=False)`:** skips lectures whose meta.json has `hidden: true`, including their logged asks and attempts. `/api/lecturer/stats` and `/api/graph` use it, so demo01 is gone from both.
- **`seed_demo_data()`:**
  - Uses only visible lectures (econ01, psych02) and raises if there are none.
  - Picks one hard snippet per lecture (preferring "Positive vs. normative analysis" and "Split brain…"; otherwise the middle snippet) with 22 attempts each at ~30% correct. Every other snippet is capped at a fail rate of 1/3.
  - Gap questions are now economics and psychology ones, including "What is price elasticity?".
  - Returns `hard_snippets: [ids]`, replacing `hard_snippet`.
  - With the repo data: econ01-010 fails at 82% and psych02-033 at 77%; the next highest is 33%.
- **tests/test_store.py:** now runs on a tmp lectures dir with two visible lectures and one hidden. `test_lecturer_stats` compares against rows filtered by lecture. New tests cover hidden-lecture exclusion, seeding per visible lecture, the real repo lectures, and the "no visible lecture" error.

### ai-status (contract update)
- Now `{mode, provider, preset, model, can_generate, coverage_check}`.
- **preset fallback:** **T2's `settings.public()` has no `preset` field yet** (it returns api_key, base_url, max_input_chars, mode, model, provider). Until it does, the API derives `preset` from the base URL: openrouter.ai → "openrouter", port 11434 or "ollama" → "ollama", port 1234 or "lmstudio" → "lmstudio".
- The live server now reports `{"mode":"api","provider":"openai_compatible","preset":"openrouter","model":"anthropic/claude-haiku-5.5","can_generate":true,"coverage_check":true}`.

### 4. Live check (one AI call)
- **Server:** `uvicorn api:app --port 8767`, PID 11696. Health: `{"ok":true,"mode":"real","status":"ready"}`, so real mode is confirmed.
- **Question:** POST /api/ask "What is price elasticity?" → **`not_covered`** (no snippet, homework false) in 2.5 s.
- **Cleanup:** stopped by PID; port 8767 closed. Deleted its rows from data/rewind.db (1 ask, 1 gap; student_id "t3-live-check").
- The covered question was checked in round 4 (found econ01-008), so no second call this time.

### Tests
`pytest tests/test_api.py tests/test_store.py tests/test_ingest.py`: **91 passed**. All mock the LLM; no live calls.
