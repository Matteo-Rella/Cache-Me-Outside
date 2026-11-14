# Round 5 prompts: student chat + professor's slides

Start after round 4 has finished.

## The feature

**Slides.** The lecturer uploads the official slides (PDF, or PPTX) per lecture. Rewind extracts each slide's text and an image, and:
- matches slides to clips, so every clip is shown with the slide the prof was on;
- searches slides too: if a question is answered in the slides but not in the recording, the student sees the slide ("Covered in the slides, not in the recording");
- gives the chat the slides as extra knowledge.

**Chat.** Under each clip: "Ask a follow-up". The AI answers only from the course materials (the clip, the rest of that lecture's transcript, and the slides) and cites them as clickable [12:40] (jumps the video) and [Slide 7] (opens the slide). Homework-style questions get hints and questions back, never solutions. Follow-ups are logged so the lecturer sees which clips cause the most follow-ups.

**Outside the materials:** a lecturer setting, chat_policy:
- "strict" (default): "This isn't in your course materials. I've flagged it to your lecturer." and flags it as a gap.
- "labelled": a short general answer under a clear "Not from your course materials" label, and still flagged.

Chat needs a live AI, so it only shows in API mode (including local Ollama). In package/none mode the chat button is hidden; slides still work.

RULES (all terminals): one shared folder (Cache-Me-Outside), no worktrees, no commits. Do NOT run any git command. Only edit the files listed as yours; anything else goes in your NOTES file. Run only your own tests. Never kill processes you didn't start.

## Contract

Data:
- data/lectures/<id>/slides/slides.json: [{"n": 1, "title": "...", "text": "...", "image": "slides/slide-001.png"}]; images next to it.
- snippets.json: each snippet may get "slides": [3, 4] (best-matching slide numbers, from alignment). Optional field; everything must work without it.

Python:
- rewind/slides.py (T2): ingest_slides(lecture_id, path) -> count. PDF with PyMuPDF (text + PNG per page at ~1280 px wide). PPTX with python-pptx for text; for images, convert to PDF with LibreOffice (soffice --headless --convert-to pdf) if installed, else text only and tell the user to export a PDF. Then align(lecture_id): embed slides and snippets with the search model, set each snippet's "slides" to the top 1–2 slides above a threshold, save. load_slides(lecture_id) -> list.
- rewind/search.py (T2): index slides too. answer(question) keeps its shape and adds "slides": [{"lecture_id", "n", "score"}] (top 2 over threshold). New status "slide_only" when no clip passes but a slide does (best None).
- rewind/llm.py (T2): chat(system, messages, max_tokens=800, stream=False) -> str, or an iterator of text chunks if stream=True. All providers. llm.available() unchanged.
- rewind/settings.py (T2): add chat_policy ("strict" | "labelled", default "strict").
- rewind/chat.py (T4): respond(student_id, snippet_id, messages) -> iterator of events: {"type": "text", "text": "..."} chunks, then {"type": "done", "citations": [{"kind": "time", "lecture_id", "t"} | {"kind": "slide", "lecture_id", "n"}], "outside_materials": bool, "homework": bool}. Citations appear in the text as [mm:ss] and [Slide N].
- rewind/store.py (T3): chat_messages(id, student_id, snippet_id, role, content, outside_materials, created_at); log_chat(...); lecturer_stats gains "followups" per snippet.

HTTP (T3):
- POST /api/chat {student_id, snippet_id, messages: [{role, content}]} -> text/event-stream, one JSON event per message (the chat.respond events). 409 if chat isn't available.
- GET /api/ai-status gains chat_available: bool.
- POST /api/lectures/{id}/slides (multipart file, local-only) -> {count, aligned}
- GET /api/lectures/{id}/slides -> slides.json (public)
- /media/{id}/slides/{file} serves slide images (allow the slides/ subfolder, still block traversal)
- /api/ask responses include "slides" (with image URL and title) and may have status "slide_only".
- python -m rewind.ingest gains --slides <path>.

---

## T2 (Codex) – slides, slide search, chat in llm

Yours: rewind/slides.py, rewind/search.py, rewind/llm.py, rewind/settings.py, tests/test_slides.py, tests/test_search.py, tests/test_llm.py, NOTES-search.md.
Implement the Python contract parts marked T2. Append PyMuPDF and python-pptx to requirements.txt. Streaming for each provider with its SDK's streaming API; mocked in tests. For the test PDF, generate a 3-page PDF in the test itself. Also find the official slides or lecture notes PDF for MIT 14.01 lecture 1 (econ01) on MIT OpenCourseWare, download it into data/lectures/econ01/source/ (gitignore note in NOTES), run ingest_slides + align, and report which clips got which slides. Stop.

## T4 (Claude Code, Opus) – the chat brain

Yours: rewind/chat.py, tests/test_chat.py, tests/chat_eval.json, tools/eval_chat.py, NOTES-lectures.md.
Build respond() per the contract, using llm.chat with stream=True.
Context: the current clip's transcript first, then the rest of that lecture's transcript as "[mm:ss] text" lines, then its slides as "[Slide N] title: text". Trim to fit llm.max_input_chars(), keeping the clip and nearby segments.
System prompt rules (constant CHAT_PROMPT, tune it):
- Answer only from the materials. Short (max ~120 words), plain language, same language as the student.
- Cite every claim with [mm:ss] or [Slide N]. Prefer pointing back to the professor ("He explains this at [14:02]").
- Homework or exam-style tasks: don't solve; give one hint and ask the student a question back.
- Outside the materials: follow settings.chat_policy (strict: say so and that it was flagged; labelled: short general answer starting with "Not from your course materials:").
- Never invent timestamps or slide numbers: after generation, check every cited time is inside the lecture and every slide exists; drop invalid ones from citations.
Detect outside_materials (ask the model to end with a hidden marker line you strip, or a cheap second call) and flag via store.flag_gap.
Eval: 15 follow-ups on econ01 and psych02 (clarifications, "why" questions, an example request, 3 outside the materials, 2 homework-style, 2 that only the slides answer). tools/eval_chat.py runs them (needs a key: if none, act as the model yourself by reading the exact prompt the code builds, like in round 3) and checks citations are valid and policy is respected. Report in NOTES-lectures.md. Stop.

## T3 (Claude Code, Sonnet) – chat API, slide endpoints, store

Yours: api.py, rewind/store.py, rewind/ingest.py, tests/test_api.py, tests/test_store.py, tests/test_ingest.py, NOTES-store.md.
Implement the HTTP contract and the store parts. Chat uses Server-Sent Events (StreamingResponse, media type text/event-stream); log the student message before streaming and the full assistant message when done. Upload endpoint is local-only and saves the file to data/lectures/<id>/source/ before ingesting. Add followups to lecturer_stats and to /api/lecturer/stats. Extend seed_demo_data with ~30 chat messages so the lecturer view shows follow-ups. Mock rewind.chat and rewind.slides in tests. Stop.

## T1 (Claude Code, Opus) – chat panel + slides in the UI

Yours: web/, NOTES-web.md.
1. Answer view: the matched slide next to the clip (side by side at 1280 px, stacked on phones), click to enlarge. "slide_only" status: show the slide(s) with "Covered in the slides, not in the recording".
2. Chat panel under the clip ("Ask a follow-up"), only if /api/ai-status says chat_available. Streams the reply token by token (fetch + ReadableStream reading the SSE events). Render [mm:ss] as chips that seek the player and play from there, [Slide N] as chips that open the slide. Show "Not from your course materials" replies with a distinct muted style, and a small "Sent to your lecturer" note when flagged. Keep history per clip in memory; send the last 10 messages.
3. Keep the order: clip → chat → "Check yourself". The quiz stays visible; chatting doesn't reset it.
4. Lecturer page: "Most follow-ups" column/sort, and per lecture an "Upload slides" control (local-only; hide it if the endpoint returns 403).
5. AI settings page: chat_policy choice (strict / labelled) with one-line explanations.
6. Mock all of it in web/js/mock.js (streamed fake reply with citations). Screenshot every state at 1280×720 and 390×844, fix what looks off. Stop.

---

## Integration additions

When integrating, also: update AGENTS.md data contracts with slides.json and the optional "slides" field, the chat event format and the new answer() status; add data/lectures/*/source/, data/settings.json and data/rewind-fake.db to .gitignore.
