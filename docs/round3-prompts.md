# Round 3 prompts: test on YouTube lectures without an API key

No API key until the event gives us one (provider unknown). Plan:
- The app must run without a key: search uses the similarity threshold only, homework detection uses keywords only.
- For testing, the coding agent itself plays the LLM for the snippet maker: the code exports the exact prompt + transcript, the agent writes the raw JSON answer by following the prompt, and the code runs its normal validation on it. Same pipeline, no API call.
- When the key arrives: set .env and rerun the snippet maker normally.

RULES (all terminals): one shared folder (Cache-Me-Outside), no worktrees, no commits. Do NOT run any git command. Only edit the files listed as yours; anything else goes in your NOTES file. Run only your own tests. Never kill processes you didn't start (no `taskkill /IM python.exe`); stop your own server with Ctrl+C or by its PID.

---

## T2 (Codex) – provider-agnostic LLM + no-key fallback

Yours: rewind/llm.py, rewind/config.py, .env.example, tests/test_llm.py, and small fallback changes in rewind/search.py and rewind/quiz.py. Notes in NOTES-search.md.

1. Make rewind/llm.py provider-agnostic: LLM_PROVIDER=anthropic|openai|gemini, LLM_API_KEY, LLM_MODEL in .env (keep ANTHROPIC_API_KEY/ANTHROPIC_MODEL as fallbacks). Same ask_json(system, user, max_tokens) signature for all three. Official Python SDKs, imported lazily so only the one in use must be installed; append them to requirements.txt. Use JSON mode where supported.
2. llm.available() -> bool. config.get_model() must not raise at import time when nothing is set.
3. Without a key: search.answer skips the coverage check and uses the threshold only (still returns the same shape); quiz.is_homework uses keywords only. One warning at startup, not per call.
4. .env.example with the new variables and a comment per provider.
5. Tests with mocked SDKs for all three providers and the no-key path. Your tests, tests/test_search.py and tests/test_quiz.py must pass. Stop.

---

## T4 (Claude Code, Opus) – real YouTube lectures, agent as the LLM

Yours: rewind/snippet_maker.py, data/lectures/ (new folders only), tests/test_snippet_maker.py, NOTES-lectures.md.

1. Add two CLI options to the snippet maker, sharing all existing code:
   - --export-prompt <file>: fetch/load the transcript, save transcript.json + meta.json as usual, then write the exact system prompt (SNIPPET_PROMPT) and user message the LLM would receive to <file> (one file per window if the transcript is split). No API call.
   - --import-raw <file>: read raw LLM output (the JSON the model would have returned) and run the normal post-processing (snapping, overlaps, validation, text, ids), then save snippets.json and print the table.
   Tests for both.
2. For econ01 (MIT 14.01 lecture 1) and psych02 (the Yale PSYC 110 lecture you picked): run --export-prompt, then act as the LLM yourself: read the exported prompt and write the raw JSON answer following SNIPPET_PROMPT exactly (as strictly as a model call would: only what the professor said, 45–120 s cuts on whole thoughts, 2 questions each). Save it as data/lectures/<id>/raw_llm_output.json and run --import-raw on it.
3. Review the result like in round 2 (cuts, concepts, summaries, questions). If something is off because of the prompt rules, improve SNIPPET_PROMPT; if it's your own answer, fix the raw JSON. Rerun import.
4. Search check without a key: python -c "from rewind import search; search.index_all()", then for the 12 demo questions in NOTES-lectures.md print the top 3 of search.search(q) with scores. Record hit@1/hit@3 and the scores of the 2 not-covered questions, and suggest a SIMILARITY_THRESHOLD in your notes (don't edit config).
5. Notes: what you changed, the results, and the exact commands to regenerate both lectures with a real key later. Stop.

---

## T3 (Claude Code, Sonnet) – ingest command for real recordings

Yours: rewind/ingest.py, tests/test_ingest.py, api.py (only to fix issues you find), NOTES-store.md.

Later we'll demo a real lecture recording with no transcript. A teammate is writing the transcriber; it outputs the transcript.json contract from AGENTS.md. Build the one command that takes a video file to a searchable lecture:

  python -m rewind.ingest --video C:\path\lecture.mp4 --lecture-id ai01 --title "..." --course "..." --lecture-number 1 [--transcript path\to\transcript.json] [--raw path\to\raw_llm_output.json]

Steps:
1. Copy the video to data/lectures/<id>/video.mp4 (keep the extension), write meta.json with video_url "media/<id>/video.<ext>".
2. Transcript: if --transcript is given, validate it with transcript_io.load_transcript and copy it in. Otherwise try faster-whisper (if installed) with model "small", device auto (CUDA if available), and convert with transcript_io.from_whisper; print progress. If neither works, stop with a clear message.
3. Snippets: if an LLM is available (llm.available(), being added by T2 — use getattr so it doesn't crash if missing), run the snippet maker; else if --raw is given, use the snippet maker's import path (T4 is adding --import-raw; call its function, check NOTES-lectures.md for the name); else export the prompt and tell the user what to do.
4. Index with search.index_lecture.
5. Print a summary: duration, segments, snippets, and the URL to open (http://localhost:8000/).
Tests with mocked transcriber and LLM using a tiny dummy file.
Also: add data/rewind-fake.db to your notes for .gitignore, and check api.py starts in real mode with no .env once T2's fallback lands (search works, threshold-only). Stop.

---

## T1 (Claude Code, Opus) – phone layout, QR entry, local video, real data run

Yours: web/, NOTES-web.md.

1. Phone layout: students will open the site from a QR code on the last lecture slide, so the student page must work well at 390×844. Check with Playwright, fix layout, tap targets, video sizing.
2. QR entry: web/qr.html, a full-screen page for projecting: big QR code (vendored JS QR library) pointing to the student page URL (taken from window.location, editable in a small field), the Rewind name and "Stuck on something from today? Ask the lecture." Explain in notes that phones need the laptop's LAN IP (uvicorn --host 0.0.0.0) and the same Wi-Fi.
3. Local video: test the "file" video type with any small public-domain mp4 (e.g. from the Blender open movies or Wikimedia Commons) in a throwaway lecture folder data/lectures/test-file/ with a fake meta.json and 2 snippets; make sure clip start, stop and replay work. Delete that folder afterwards.
4. Lecture context: the hero should show which lectures are searchable ("Searching 2 lectures: Microeconomics · Psychology"), from /api/lectures.
5. When T4 has written econ01 and psych02, run uvicorn api:app --port 8000 (real mode, no key), ask the demo questions from NOTES-lectures.md on the website, screenshot every state, and fix anything that looks off with real content (long concept names, long summaries, many lectures in the graph). Stop.
