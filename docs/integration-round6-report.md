# Rewind Round 6 Integration Report

**Date:** 2026-11-14  
**Scope:** Diagnostic verification of combined Round 6 work across `web/` (T1), `rewind/llm.py` / `rewind/settings.py` / `rewind/slides.py` / `rewind/search.py` (T2), `api.py` / `rewind/store.py` / `rewind/ingest.py` (T3), and `rewind/chat.py` / `rewind/snippet_maker.py` / `rewind/context_package.py` (T4).  
**Environment:** `..\cmo-venv\Scripts\python.exe` (Python 3.15.2). No application code was modified, no Git commands were run, no real lecture data/settings/databases were touched, and no paid AI calls were made.

---

## 1. Test results and regressions

- **Baseline:** 327 passing tests (per `docs/handover-codex.md` and `docs/coordination-round6.md`).
- **Combined isolated `pytest` run (`LLM_MODE=api` with dummy credentials, isolated temporary `DATA_DIR` / `LECTURES_DIR` / `DB_PATH`):**
  - **472 passed, 1 warning in 7.85s** (+145 tests over the 327 baseline; **0 regressions**).
  - Warning: `PyMuPDF` (`pymupdf/__init__.py:470`) emits `DeprecationWarning: 'typing.ByteString' and 'collections.abc.ByteString' are deprecated and slated for removal in Python 3.17` on Python 3.15.2.
- **Environment-sensitivity finding (`LLM_MODE=none`):**
  - Running the same isolated suite with `LLM_MODE=none` yields **471 passed, 1 failed**: `tests/test_quiz.py::test_homework_llm` fails because it mocks `rewind.llm.ask_json` without mocking `rewind.llm.available()` (so `quiz.is_homework` skips `ask_json` when `available()` is `False`).

---

## 2. Browser/API integration results

Verified against a live `uvicorn` FastAPI server (`api.create_app(fake=False, ...)`) using isolated temporary fixtures, a real 3-page PDF ingested via `rewind.slides.ingest_slides`, real `rewind.chat.respond`, real `rewind.store`, real `rewind.settings`, and mocked `rewind.search.answer` / `rewind.llm.chat` / `rewind.llm.test_connection`:

- **Real mode confirmed (no silent mock fallback):** `/api/health` returned `{"ok": true, "mode": "real", "status": "ready"}`; the student page rendered without the `"Demo data"` badge or `?mock=1`.
- **Student clip, slides, and lightbox:** Asking a covered question rendered the clip (`"Supply curve and opportunity cost"`), displayed the matched slide thumbnail in `.sl-aside`, and opened the slide lightbox modal (`.modal-slide .sl-big`) on click.
- **Follow-up chat SSE & citations:** Streamed SSE replies with `[00:15]` and `[Slide 1]` split across chunk boundaries rendered as clickable `.ct-time` and `.ct-slide` chips; clicking `[Slide 1]` opened the slide lightbox.
- **Interrupted stream handling:** Simulated mid-stream `RuntimeError` rendered `.ct-ai.is-error .ct-err-note` (`"The AI couldn't answer right now (RuntimeError: Simulated provider interruption)."`) instead of presenting a partial reply as complete.
- **Quiz, `slide_only`, and `not_covered`:** Interactive quiz completed (`2/2`); `slide_only` rendered `.sl-only` with Slide 3; `not_covered` rendered `.notcovered` and logged the gap.
- **Lecturer dashboard:** Showed the logged gap (`"What is quantum chromodynamics?"`), resolved it, rejected an invalid `.txt` slide upload with HTTP 400 (`.up-status.is-err`), and accepted a valid 3-page PDF upload.
- **Settings persistence & `none` mode:** `chat_policy` (`labelled`) persisted across page reload via `/api/settings`; `Generate snippets` displayed the `/api/lectures/{id}/generate/estimate` confirmation (`1 AI call, about $0.32`, token counts, cancelable); switching to `No AI` mode hid `.ct-card` on the student page.
- **Console / runtime errors:** `0` uncaught `pageerror`s. Only console error was the expected HTTP 400 from the invalid `.txt` slide upload test.

---

## 3. Blocking bugs and exact affected files

**None** for the core local demo with PDF slides (or no slides) and configured API / package / none modes. All primary student, lecturer, and settings flows work end-to-end against the live FastAPI backend.

*(Note: If a demo relies on PPTX decks without LibreOffice installed—producing text-only slides with `image_url: null`—see Nonblocking Issue #2 in `web/js/slides.js`.)*

---

## 4. Nonblocking issues

1. **Boolean `aligned` rendered as a count in lecturer slide upload:**  
   - `api.py` (`POST /api/lectures/{id}/slides`) returns `aligned: bool`, whereas `web/js/lecturer.js` (line 58) formats `r.aligned` as a count: `"3 slides · matched to true clips"` (observed in Playwright).
2. **Text-only slides (`image_url: null`) filtered out in frontend:**  
   - `api.py` (`/api/ask` and `/api/lectures/{id}/slides`) preserves `text` when `image_url` is `null` for text-only PPTX decks, but `web/js/slides.js` (`resolve` at line 37, `openSlide` at line 42, and `slideOnlyCard` at line 110) filters out slides without `image`, hiding them in `slideAside` and showing `"The slide image isn't available."` in `slideOnlyCard`.
3. **Generate estimate `scope` and `null` pricing not surfaced in UI:**  
   - `api.py` (`/api/lectures/{id}/generate/estimate`) returns `scope` (`"Initial snippet generation; verification, repair and retries are excluded."`) and nullable `est_cost_usd` (`null` for unknown pricing). `web/js/settings.js` (lines 426–440) safely avoids displaying `null` as `$0.00`, but does not display `est.scope` or an explicit `"pricing unknown"` note. `preprocess_model` is also not exposed in the settings UI (env/API only).
4. **Missing dependency declaration:**  
   - `python-multipart` is installed in `cmo-venv` and required for `POST /api/lectures/{id}/slides`, but is not yet listed in `requirements.txt`.
5. **Test isolation fragility in `tests/test_quiz.py`:**  
   - `tests/test_quiz.py::test_homework_llm` fails under `LLM_MODE=none` because it does not mock `rewind.llm.available()`.
6. **Interpreter version & `.gitignore` hygiene:**  
   - `cmo-venv` runs Python 3.15.2 (vs `AGENTS.md` Python 3.11), producing a `PyMuPDF` `ByteString` deprecation warning.  
   - `.gitignore` does not yet ignore `data/settings.json`, `data/rewind-fake.db`, `**/embeddings.ids.json`, `data/lectures/*/video.*`, or `data/lectures/*/snippet_prompt.md`.
7. **Professor approval enforcement:**  
   - Intentionally deferred to a separate coordinated task; generated/verified snippets are not yet gated by a professor-approved publication state.

---

## 5. Smallest recommended repair tasks

1. **`web/js/lecturer.js` (line 58):** Update `r.aligned` status text to treat `r.aligned` as a boolean (e.g., `r.aligned ? " · clips matched" : ""`) rather than interpolating `true`/`false` as a number.
2. **`web/js/slides.js` (lines 28–38, 109–115):** Allow slides with `s.image || s.text` in `resolve()`, and render a readable text fallback card when `s.image` is empty.
3. **`web/js/settings.js` (lines 426–440):** Append `est.scope` to the generate confirmation details and show `"pricing unknown"` when `est.est_cost_usd == null`.
4. **`requirements.txt` & `tests/test_quiz.py`:** Append `python-multipart` to `requirements.txt`, and mock `rewind.llm.available` in `tests/test_quiz.py::test_homework_llm`.
5. **`.gitignore`:** Add `data/settings.json`, `data/rewind-fake.db`, `**/embeddings.ids.json`, `data/lectures/*/video.*`, and `data/lectures/*/snippet_prompt.md`.

---

## 6. Overall demo readiness

**Ready for local demo** (with PDF slides or no slides, and mocked/configured API, package, or no-AI modes). The combined test suite passes (**472/472**), and live FastAPI + Playwright verification confirms the student, lecturer, and settings interfaces work end-to-end without silent mock fallback. The 5 small repair tasks above are low-risk polish items that can be applied before or after the demo, followed by the separate professor-approval enforcement task.
