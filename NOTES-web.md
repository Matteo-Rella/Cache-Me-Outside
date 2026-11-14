# NOTES-web: Rewind website (web/)

Plain HTML, CSS and vanilla JS modules. No framework, no npm, no build step.

## Run it

- With the API: `uvicorn api:app --port 8000`, then open http://localhost:8000/ (student) and /lecturer.html.
- Front end only: `python -m http.server 5500 -d web`, then open http://localhost:5500/?mock=1.
- Front end on 5500 talking to the API on 8000: http://localhost:5500/?api=http://localhost:8000

## Files

| File | What it does |
|---|---|
| `index.html`, `js/student.js` | Student page: name modal, question box, example chips, loading state, clip, homework note, not-covered card, "Check yourself" quiz, rewatch panel |
| `lecturer.html`, `js/lecturer.js` | Lecturer page: metric cards, "Where students get stuck", uncovered questions with Resolve, knowledge map, "Load demo data" |
| `js/player.js` | `ClipPlayer`: plays only [start, end]. YouTube via the IFrame Player API; local files via `<video>`. Custom clip-only progress bar, play/pause, "Replay clip", pause at the end |
| `js/api.js` | API client and mode detection |
| `js/mock.js`, `js/mock-data.js` | In-browser mock of every endpoint, built on a copy of demo01 |
| `js/ui.js` | Element builder, icons, time format, shared header, modal, toast |
| `css/style.css` | All styles. One accent colour (`--accent: #4f46e5`), Inter for UI, Instrument Serif for headings |
| `vendor/` | Inter and Instrument Serif (woff2, OFL) and vis-network 9.1.9. Everything works offline except YouTube. |
| `screenshots/` | Playwright screenshots, git-ignored (`.gitignore` with `*`). `screenshots/api/` was taken against api.py. |

## Mock mode

- **When it's used:** the URL has `?mock=1`, or `GET /api/health` fails or times out (2.5 s).
- **"Demo data" tag:** shown in the header when mocked, and also when the API reports `mode: "fake"`.
- **What it covers:** every endpoint in the contract.
  - Keyword search over the 3 demo01 snippets, with a 1.4–2.3 s delay so the loader shows.
  - Homework detection with a regex.
  - Grading with the real answer indexes.
  - Rewatch list, stats, gaps (resolve works) and seed.
  - A graph with a few hand-written shared concepts (linear equations, matrix, vectors) so the map shows links.
- **State:** kept in `localStorage["rewind.mock.state"]`. "Load demo data" resets it.
- **Data:** `mock-data.js` is a copy of `data/lectures/demo01`. If demo01 changes, regenerate it (it's a straight JSON dump into two exports).

## Tested

- **Mock mode:** every state at 1280×720 with Playwright: name modal, home, loading, found, wrong answer with the "See it explained another way" offer, question 2, quiz result, rewatch panel, not covered, homework, lecturer page, clip modal, resolve, graph. No console errors.
- **Real api.py (fake mode, port 8000):** same flows, every endpoint called through the UI. Shapes match the contract and there were no console errors.

## For the API (api.py / T3)

Nothing blocks the front end. These would improve the demo:

1. **Example questions read badly.** `/api/examples` gives "What is Three pictures of a linear system?" and "How does Coefficient matrix of a 2x2 system work?". Lower-casing the concept helps ("What is the coefficient matrix of a 2x2 system?"). Hand-picked examples per lecture would be better.
2. **The fake search matches off-topic questions.** "What was the Treaty of Westphalia?" and "How do I compute the determinant of a 3x3 matrix?" both came back `found`. This matters only if the demo runs in fake mode. It probably matches on common words ("what", "the", "matrix"), so it needs stop-words and a minimum score.
3. **`/api/graph` labels.**
   - Lecture nodes use the full YouTube title ("Lec 1 | MIT 18.06 …"). The front end now shows "Lecture N" when `lecture_number` exists.
   - Each snippet gets a concept node with the same text as the snippet, so the map shows every name twice. The front end now drops concept nodes that only repeat their snippet's name (`tidyGraph` in lecturer.js).
   - The map gets interesting only when concept nodes are shared between snippets or lectures. Ideally `/api/graph` would emit broader concepts, for example a `topics` field per snippet added by the snippet maker.
4. **`video_url` for file lectures:** the player loads `media/<id>/<file>` relative to the page (or to `?api=`). Keep it relative, without a leading host.

## Round 3

### Phones (QR code from the last slide)
- **Checked at 390×844 with Playwright:** no sideways scroll, and tap targets are 40–48 px.
- **Phone layout changes** (`@media (max-width: 640px)` in style.css):
  - The header is compact: the "demo data" tag becomes an amber dot, and the name chip shows only an icon.
  - The ask button shows only its icon, and the question box grows to fit long questions.
  - Video is full width, with icon-only replay.
  - Options, the alternative clip and "Next question" stack in one column at full width.
- **Lecturer page on phones:** also usable (header row hidden, smaller numbers).

### QR page: `web/qr.html`
- **What it shows:** a full-screen page for projecting, with a big QR code (qrcode-generator 1.4.4, MIT, vendored in `vendor/qrcode/`), "Rewind · Ask your lecture." and "Stuck on something from today? Ask the lecture."
- **The URL:** comes from `window.location` (the student page next to qr.html). Hover the bottom of the screen to edit it; the change is remembered in localStorage.
- **Phones can't open `localhost`.** For the demo:
  1. Start the API reachable from the network: `uvicorn api:app --host 0.0.0.0 --port 8000`.
  2. Find the laptop's Wi-Fi IP: `ipconfig` → "IPv4 Address" under the Wi-Fi adapter, e.g. 192.168.1.23.
  3. Open `http://192.168.1.23:8000/qr.html` on the laptop. The QR then already points to the right address; otherwise type it in the field. The page warns when the URL is localhost.
  4. Phones must be on the same Wi-Fi. Windows may ask to allow Python through the firewall: allow it for private networks. University and guest Wi-Fi often block device-to-device traffic, so a phone hotspot from the laptop is the safest option.

### Local video files
- **Test lecture:** a throwaway `data/lectures/test-file/` with a 10 s Big Buck Bunny mp4 (CC-BY, Blender Foundation), a fake meta.json and 2 snippets. Deleted afterwards.
- **API:** `/media/` answers range requests with 206 and blocks `..` (404).
- **Player:** start, stop at the end, replay, play-after-end and seeking all work.
- **Bugs fixed in the player:**
  - `onEnd` fired twice per end.
  - Playback ran ~0.25 s past the end, because `timeupdate` fires only 4× a second. The file player now checks every animation frame and stops within 0.05 s.

### Searchable lectures in the hero
- The line above the hero now reads "Searching 3 lectures: Linear Algebra · Microeconomics · Psychology". The loader uses the same text.
- Subject names come from `course`, with the university, course code and "Principles of" / "Introduction to" removed (`subjectName` in ui.js). Only lectures that have snippets are counted.
- Clip headers and lecturer rows now say "Psychology · Lecture 2 · 23:41–24:46" instead of just "Lecture 2", because several lectures can share a number.

### Real lectures (econ01 + psych02, `uvicorn api:app --port 8000`, real mode)
NOTES-lectures.md had no demo questions yet, so I used 6 per lecture following the same recipe. Every one was asked on desktop and phone, with screenshots in `web/screenshots/real/`. There were no console errors.

| Question | Result | Right? |
|---|---|---|
| Why does a supply curve slope upward? | Supply curve and market equilibrium | yes |
| Should people be allowed to sell their kidneys? | The economic case for selling kidneys | yes |
| What does it mean that everything has a trade-off? | Opportunity cost | yes |
| Why are diamonds expensive when water is so useful? | Water-diamond paradox | yes |
| What is the price elasticity of demand? *(not covered)* | Demand curve | **no**, should be not covered |
| Calculate the equilibrium price if demand is Q = 100 - 2P… | Supply curve… + homework note | yes |
| How do neurons talk to each other? | Synapses and neurotransmitters | yes |
| What happens if you cut the connection between the two halves of the brain? | The cortex and its four lobes | **no**, psych02-033 "Split brain" exists |
| Why did Descartes think the mind is separate from the body? | I think: why the mind seemed separate from the body | yes |
| Can someone see fine but not recognise faces? | Agnosia, prosopagnosia and sensory neglect | yes |
| Why do we need to sleep? *(not covered)* | not covered | yes |
| Solve exercise 3: is alcohol an agonist or an antagonist? Write the answer for me | Curare and alcohol… + homework note | yes |

The two misses are for search.py / the threshold, not the website. The first ask after the API starts takes about 9–23 s while the embedding model loads. Warm the server up with one question before the demo.

**Fixed for real content:**
- **"Where students get stuck":** shows the top 8, with "Show all 59 moments". The fail rate shows "–" when a clip has no attempts.
- **Knowledge map with many clips** (59 across 3 courses was an unreadable hairball): when there are more than 20 clips, it shows only the clips students asked about (top 16), each lecture node says "Psychology · Lecture 2 · 37 clips", and labels wrap. With 20 or fewer clips it shows all of them as before.
- **Long concepts and ~400-character summaries** wrap fine on desktop and phone. No change needed.

**For the API (in addition to the earlier list):**
- `/api/graph` still gives one concept node per snippet with the same name as the snippet. The front end drops those, so the map has no cross-lecture links. Shared topic nodes would make it much more interesting.
- **Test data in the real database:** my test asks (about 35) are now logged in `data/rewind.db` and show up in the lecturer stats. "Load demo data" or deleting the database before the demo will clear them.

## Round 4: AI settings page

New files: `web/settings.html`, `web/js/settings.js`. Changed: `web/js/api.js` (new endpoints, `downloadPackage`, errors carry `.status`), `web/js/mock.js` (mocks appended at the end), `web/js/ui.js` (an "AI settings" link in the header on the lecturer and settings pages only, never on the student page), `web/js/student.js` (reads `/api/ai-status` quietly). The CSS is appended at the end of `style.css` under "Round 4 · AI settings page", using only existing variables with no hard-coded colours. The rules above it are byte-identical (md5 checked).

**What the page does:**
- **Three mode cards:** "Use an API", "Use any AI chat or agent" (badge "No key needed") and "No AI". The mode in use is marked "In use", and a pill in the top right shows the saved mode, provider and model. Picking a card switches the panel below; anything not yet saved is marked "Not saved yet".
- **API panel:**
  - **Provider and model:** a provider dropdown, and a model field with suggestions per provider (clickable chips plus a datalist; any model name works).
  - **Key field:** masked, with placeholder "sk-…a1b2 saved · leave empty to keep it", a show/hide button and a "Where do I get a key?" link.
  - **Base URL:** shown only for OpenAI-compatible, with one-click hints for Ollama (`http://localhost:11434/v1`), LM Studio (`http://localhost:1234/v1`) and OpenRouter.
  - **Buttons:** "Test connection" saves first (the contract's test endpoint uses the saved settings), then shows a green or red message with latency. "Save" saves only. The key is sent only if typed (`api_key` omitted = keep current).
- **Per lecture, API mode:** "Generate snippets" calls POST generate, then polls `/generate/status` every 1.2 s. While it runs there's a moving progress bar and the status message. When done it shows "N snippets saved · Open student page" and refreshes the moment count; errors appear in red.
- **Per lecture, package mode:**
  - "Download package" (a real zip) and "Copy prompt · part 1 of 3" buttons, one per part.
  - A large paste box (prose and json code blocks are fine, several parts can be pasted one after another), "Upload file" (.json/.txt/.md) and "Import".
  - **On errors:** "The answer has N problems. Nothing was changed.", a list of the first 8, and "Copy fix message for your AI".
  - **On success:** "N moments imported. Open student page".
- **No AI:** a check list of what still works and what doesn't (new snippets).
- **Local-only:** if `/api/settings` returns 403 the page shows "Open this page on the Rewind computer". Any other failure (endpoint missing) shows "AI settings aren't available yet". If api.py also guards settings.html itself, a phone just gets the server's 403 page, which is fine.
- **Student page:** reads `/api/ai-status` and only stores `data-ai-mode` and `data-coverage-check` on `<body>`. Nothing visible changes in any mode.

**Mock mode** (`?mock=1`): every new endpoint is mocked.
- **Settings:** kept in localStorage, with the key masked as `sk-…a1b2`.
- **Test connection:** a key containing "bad" or "wrong" gives a 401-style failure, and an LM Studio URL gives "server not running"; anything else succeeds.
- **Prompt parts and package:** 3 prompt parts built from demo01, and the package is a real zip (START-HERE.md, AGENT.md, meta.json, PROMPT-part-1..3.md) made by a tiny zip writer in mock.js.
- **Import:** pulls the JSON out of chat replies (code fences, prose, several blocks), validates concept, times, 2 questions, 4 options and answer 0–3, and writes the same kind of fix message the contract describes.
- **Generate:** runs about 7 s with three status messages.

**Checked** at 1280×720 with Playwright (`web/screenshots/settings/`):
- **API mode:** the default view, test ok, test failed (bad key), OpenAI-compatible with hints, then test ok.
- **Generate:** running, then done.
- **Package mode:** unsaved, then saved; prompt copied (clipboard checked); zip downloaded (6 files); a broken reply giving 6 errors with the fix message copied; a good reply giving 2 moments imported.
- **No AI:** unsaved, then saved.
- **Navigation and guard:** the lecturer header shows the "AI settings" link, the student page has no link and no visible change, and the 403 and missing-endpoint states render properly.
- **Phone (390 px):** no sideways scroll. There were no console errors.

**For api.py (T3):**
- **403 detail:** a JSON `{"detail": "..."}` lets the page show it, though it doesn't need it.
- **Import result:** the page expects exactly `{ok, snippets_count, errors, fix_message}` and shows at most 8 errors (all of them are in the fix message).
- **Generate:** the page treats `{"ok": false, "error"}` from POST generate as "couldn't start", then polls `/generate/status` until state is `done` or `error`.
- **Lecture list:** the page uses `/api/lectures` for the lecture list. Hidden lectures (demo01) therefore don't appear here. If the lecturer should also manage hidden ones, add a local-only list endpoint.

## Round 4 follow-up: OpenRouter preset and cost confirm

**OpenRouter in the provider dropdown** (Anthropic, OpenAI, Google Gemini, OpenRouter, OpenAI-compatible):
- **What it is:** a preset of `openai_compatible` with the base URL fixed to `https://openrouter.ai/api/v1`, so the base URL field stays hidden.
- **Suggested model:** `anthropic/claude-haiku-5.5` comes first, then claude-sonnet-5.5, gpt-4.1-mini and gemini-2.5-flash.
- **Key:** placeholder `sk-or-…`, with a link to openrouter.ai/settings/keys.
- **What Save sends:** `{mode: "api", provider: "openai_compatible", preset: "openrouter", model, base_url: "https://openrouter.ai/api/v1", api_key?}`. The `preset` field is extra; api.py can ignore it and rely on base_url.
- **Showing the saved state:** the page selects "OpenRouter" when `/api/settings` returns `preset: "openrouter"`, or when provider is openai_compatible with an openrouter.ai base URL. The "Now:" pill reads "OpenRouter · anthropic/claude-haiku-5.5".
- **Other presets:** `ollama` and `lmstudio` show as OpenAI-compatible with their base URL filled in.

**Cost confirm before "Generate snippets":**
- The button first calls `GET /api/lectures/{id}/generate/estimate`, then shows an inline confirm with Cancel and Generate. For example: "This makes 1 AI call, less than $0.01. Generate?", with "About 2.1k tokens in, 2.7k out. Replaces the 3 current moments."
- Cost wording: under $0.01 shows "less than $0.01", otherwise "about $X.XX".
- If the estimate endpoint returns 404/501 (or fails), the page still confirms: "This sends the whole transcript to your AI provider. Generate?"

**Mock:**
- **Default settings:** OpenRouter + `anthropic/claude-haiku-5.5`.
- **Preset:** `/api/settings` and `/api/ai-status` return `preset` (worked out from base_url) and `model`.
- **`/generate/estimate`:** 1 call, input tokens ≈ transcript characters / 4, about 900 output tokens per snippet, priced at about $0.10/$0.50 per million tokens.

**Checked** at 1280×720 (`web/screenshots/settings/t01–t07`):
- OpenRouter selected on load, then test connection.
- The confirm with the estimate; Cancel brings the button back; Generate runs, then done.
- The confirm when the endpoint returns 404 (a real-mode page with mocked network routes).
- The confirm on a phone (390 px, no sideways scroll). There were no console errors.

New CSS (`.set-confirm…`) is appended at the end of style.css under "Round 4 follow-up".

## Known limits

- **YouTube controls:** the player shows YouTube's own title bar and "More videos" overlay while paused. They're dimmed by our shield but can't be removed. Clicks on the video go to our play/pause, not YouTube's.
- **Autoplay:** the clip autoplays after a question, but browsers may block that. The big play button then starts it.
- **Student id:** the lower-cased name the student types. That's fine for a demo but isn't real identity.
- **Test data in the API's fake DB:** testing left a few asks and gaps in `data/rewind-fake.db` ("photosynthesis…", "why do we write Ax = b"). "Load demo data" should clear them; if it doesn't, delete that file.
- **Dependency:** `playwright` was appended to requirements.txt (dev only, used for the screenshot checks). Chromium for it was installed with `python -m playwright install chromium`.

## Round 6 (T1)

### Completed work
- **Streamed SSE chat (`web/js/api.js`, `web/js/chat.js`):**
  - `chatStream(body, onEvent, signal)` flushes trailing UTF-8 bytes with `decoder.decode()`, releases the stream reader in `finally`, treats truncated JSON (`{...` failing `JSON.parse`) as an error event rather than raw text, and throws `AbortError` consistently when aborted in mock mode.
  - `chatPanel(...)` exposes `card.abortChat()`, tracks whether a `done` or `error` event was received, and marks streams that end without `done` as interrupted (`meta: { done: true, error: "The reply was interrupted before finishing." }`) instead of presenting partial replies as complete.
  - Aborted streams (`AbortError`) clean up empty placeholders or mark partial text as cancelled (`cancelled: true, error: "Reply cancelled before finishing."`). Cancelled/errored turns are excluded from subsequent `messages` payloads; `409` cleans up the pending turn and shows the unavailable state.
  - Added `.ct-err-note` at the end of `web/css/style.css` so partial/failed replies display an explicit error note alongside "Try again".
- **Clip isolation, citations & quiz preservation (`web/js/student.js`, `web/js/player.js`, `web/js/slides.js`):**
  - `openClip()` increments `askToken` so late `api.ask()` responses cannot overwrite a clip opened from "Rewatch before the exam".
  - `ask()`, `openClip()`, and switching to an alternative clip (`playAlt`) abort any in-flight chat stream via `abortActiveChat`.
  - `foundView()` mounts `chatPanel` for the active clip (`mountChat(snip, lec)`), binding `[mm:ss]` and `[Slide N]` callbacks to that clip's `snippet` and `lecture`; switching to an alternative clip updates the chat panel to that clip's isolated `history` entry without resetting the quiz.
  - `ClipPlayer` (`initYouTube` `onReady`) seeks to `this.start` if `setRange` was called before the player became ready. `openSlide()` shows a toast if no slide image is available.
- **Lecturer controls & settings (`web/js/lecturer.js`, `web/js/settings.js`, `web/js/mock.js`):**
  - `lecturer.js`: hides the slide upload card on `403` or `404` from `/api/settings`, guards `r.aligned != null`, and styles upload errors with `.up-status.is-err`.
  - `settings.js`: `renderChatPolicy()` no longer fakes `saved.chat_policy = p.id` when the server does not return the saved `chat_policy`; instead it displays `.cp-status.is-err` ("This server doesn't support saving chat_policy yet."). Mode/API saves preserve `preset` and `chat_policy`.
  - `mock.js`: `mockChat` respects `AbortSignal` and increments `FOLLOWUPS[snip.id]`; mock slide upload rejects non-`.pdf`/`.pptx` filenames with `400`.

### Public functions / return shapes
- `web/js/api.js`: `chatStream(body, onEvent, signal) -> Promise<void>` (expects SSE `data: <json>` events `{type: "text", text}`, `{type: "done", citations, outside_materials, homework}`, or `{type: "error", message}`), `api.slides(id) -> Promise<Array<{n, title, text, image, image_url}>>`, `api.uploadSlides(id, file) -> Promise<{count, aligned}>`.
- `web/js/chat.js`: `chatPanel({ snippet, lecture, studentId, onTime, onSlide }) -> HTMLElement` (with `.abortChat()`).
- `web/js/slides.js`: `loadSlides(lectureId)`, `slideImage(lectureId, slide)`, `openSlide(lecture, n)`, `slideAside(lecture, refs, nums)`, `slideOnlyCard(res, question)`.

### Checks
- Playwright verification (`verify_web.py`, using `C:\Users\User\Documents\GitHub\cmo-venv\Scripts\python.exe` against a local static server serving `web/`):
  - **Desktop (1280×720) & Phone (390×844) in `?mock=1`**: name modal, found clip + matched slide + lightbox enlargement, streamed follow-up with clickable `[mm:ss]` and `[Slide N]` chips, quiz intact and interactive, outside-materials strict flagging, `slide_only` card, lecturer "Most asked" / "Most follow-ups" sort + invalid slide upload error, settings `chat_policy` + package mode. Zero console/page errors and zero horizontal overflow. Screenshots saved under `web/screenshots/round6/`.
  - **Real-mode route-intercepted checks (1280×720)**: `chat_available: false` hides chat; interrupted SSE stream (CRLF, two events in one chunk, no `done`) shows `.ct-ai.is-error .ct-err-note`; `409` shows unavailable message; `settings.html` against an older server without `chat_policy` shows `.cp-status.is-err` instead of claiming saved.

### Remaining blockers & actions for other terminals
- **T2 / T3**: Live backend integration (real `rewind.chat.respond`, `rewind.slides`, `chat_policy` persistence in `rewind/settings.py` + `api.py`, and `/api/chat` SSE + `/api/lectures/{id}/slides` in `api.py`) was not tested against a live `uvicorn api:app` process here because T2/T3 are completing those modules in parallel; all backend checks in this round used `mock.js` or Playwright route interception. Coordinator should re-verify the student/lecturer/settings flow against live `api.py` once T2/T3/T4 finish.
