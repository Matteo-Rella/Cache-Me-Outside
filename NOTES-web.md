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

## Known limits

- **YouTube controls:** the player shows YouTube's own title bar and "More videos" overlay while paused. They're dimmed by our shield but can't be removed. Clicks on the video go to our play/pause, not YouTube's.
- **Autoplay:** the clip autoplays after a question, but browsers may block that. The big play button then starts it.
- **Student id:** the lower-cased name the student types. That's fine for a demo but isn't real identity.
- **Test data in the API's fake DB:** testing left a few asks and gaps in `data/rewind-fake.db` ("photosynthesis…", "why do we write Ax = b"). "Load demo data" should clear them; if it doesn't, delete that file.
- **Dependency:** `playwright` was appended to requirements.txt (dev only, used for the screenshot checks). Chromium for it was installed with `python -m playwright install chromium`.
