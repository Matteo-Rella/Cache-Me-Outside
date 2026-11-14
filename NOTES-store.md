# NOTES-store

Owned: `rewind/store.py`, `tests/test_store.py`, `api.py`, `tests/test_api.py`, this file.

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
