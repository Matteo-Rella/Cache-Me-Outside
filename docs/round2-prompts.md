# Round 2 prompts

State after round 1: everything lives in one folder (Cache-Me-Outside), nothing committed, no worktrees.
Done: shared modules, snippet maker, store, student Streamlit app, quiz. Running: search (T2).
Known mismatch: search.answer() and app.py use `"best": <snippet dict>`, but store.log_ask expects `"best": {"snippet": ..., "score": ...}`. The canonical shape is the one search.py returns.

Every prompt starts with the same rules block.

```
RULES: All agents work in this one folder (Cache-Me-Outside). There are no worktrees and no commits. Do NOT run any git command. Only edit the files listed as yours. If you need something changed in another file, write it in your NOTES file. Run only your own tests. Python env is the active venv; install packages with pip, append new ones to requirements.txt.
```

## API contract (shared by T3 and T1)

All JSON. "Public snippet" = {id, lecture_id, concept, start, end, summary, questions: [{q, options}]} (never send answer indexes, explanations or full text to the browser).
"Lecture" = {lecture_id, title, course, lecture_number, video_url, video_type: "youtube" | "file", youtube_id (or null), snippet_count}.

- GET  /api/health -> {"ok": true, "mode": "real" | "fake"}
- GET  /api/lectures -> [Lecture]
- GET  /api/examples -> ["3 example questions built from snippet concepts"]
- POST /api/ask {student_id, question} -> {status: "found" | "not_covered", homework: bool, snippet: public snippet | null, lecture: Lecture | null, alternatives: [{snippet, lecture}]}
- POST /api/answer {student_id, snippet_id, question_index, chosen} -> {correct: bool, correct_index: int, explanation: str}
- GET  /api/students/{student_id}/rewatch -> [{snippet, lecture}]
- GET  /api/lecturer/stats -> {totals: {asks, found_pct, not_covered_pct, avg_fail_rate}, snippets: [{snippet_id, concept, lecture_id, lecture_title, start, end, times_asked, attempts, fail_rate}]}
- GET  /api/lecturer/gaps -> [{gap_id, question, count, last_asked}]
- POST /api/lecturer/gaps/{gap_id}/resolve -> {ok: true}
- POST /api/lecturer/seed -> {ok: true}
- GET  /api/graph -> {nodes: [{id, label, type: "course" | "lecture" | "snippet" | "concept", snippet_id?, times_asked?, fail_rate?}], edges: [{from, to}]}
- GET  /media/{lecture_id}/{filename} -> local video file from data/lectures/<id>/, with HTTP range support
- Everything else: static files from web/ (index.html at /, lecturer.html at /lecturer.html)

---

## T3 (Claude Code, Sonnet, keep the session) – fix store + build the API

```
RULES: All agents work in this one folder (Cache-Me-Outside). There are no worktrees and no commits. Do NOT run any git command. Only edit the files listed as yours. If you need something changed in another file, write it in your NOTES file. Run only your own tests. Python env is the active venv; install packages with pip, append new ones to requirements.txt.

Two jobs. Yours: rewind/store.py, tests/test_store.py, api.py, tests/test_api.py, NOTES-store.md.

1. Fix the shape mismatch in store.log_ask. The canonical search.answer() result is the one rewind/search.py returns: {"status", "best": <snippet dict> | None, "alternatives": [<snippet dict>], "score": float}. Make log_ask read snippet_id from result["best"]["id"]. Keep accepting the old {"snippet": ..., "score": ...} form too. Update the tests.

2. Build api.py with FastAPI + uvicorn, exactly following the API contract in docs/round2-prompts.md (read it). Wrap rewind.search, rewind.store, rewind.quiz and rewind.lectures; don't reimplement their logic.
- Strip answer indexes, explanations and full text from snippets sent to the browser. /api/answer uses quiz.grade with the full snippet server-side.
- video_type: "youtube" if video_url is a YouTube link (extract youtube_id), else "file" (video_url like "media/<id>/video.mp4").
- /media/{lecture_id}/{filename}: serve from data/lectures/<id>/ with range requests (FileResponse supports them in recent Starlette; check). Block path traversal.
- /api/graph: course → lecture → snippet → concept from lectures + store.lecturer_stats. Same concept text (case-insensitive) in several lectures = one node.
- /api/examples: 3 short questions from real snippet concepts (no LLM, e.g. "What is <concept>?" / "How does <concept> work?").
- Mount web/ as static files at / if the folder exists (another agent is building it), with html=True.
- Fake mode: if REWIND_FAKE=1, or if search raises at startup (e.g. no API key), use tests/fakes.py for search and store. Report the mode in /api/health and print it at startup.
- CORS open for localhost.
- Run: uvicorn api:app --reload --port 8000
3. tests/test_api.py with TestClient in fake mode covering every endpoint, plus one test that no response leaks "answer" or "explanation" before /api/answer.
Finish: your tests pass, notes in NOTES-store.md, stop.
```

## T1 (Claude Code, Opus) – website front-end

```
RULES: All agents work in this one folder (Cache-Me-Outside). There are no worktrees and no commits. Do NOT run any git command. Only edit the files listed as yours. If you need something changed in another file, write it in your NOTES file. Run only your own tests. Python env is the active venv; install packages with pip, append new ones to requirements.txt.

Yours: web/ (everything inside), NOTES-web.md. Another agent is building api.py in parallel; follow the API contract in docs/round2-prompts.md exactly and don't edit api.py.

Build the Rewind website: plain HTML, CSS and vanilla JS modules, no frameworks, no npm, no build step. Vendor any library and font into web/vendor/ so it works offline (except YouTube and the LLM). It's demoed to a jury on a laptop projector (1280×720) and judged on creativity and polish, so it must look like a real product. English UI.

Mock mode: web/js/mock.js returns realistic responses for every endpoint using data/lectures/demo01 content copied in. Use it when the URL has ?mock=1 or /api/health fails, so you can build and test before the API is ready. Show a small "demo data" tag when mocked.

Design: name "Rewind", tagline "Ask your lecture." Calm and modern: white space, one accent colour, a good free font, rounded cards, subtle motion. Make the core idea obvious: the answer is the professor's own words.

index.html (student):
1. First visit: small modal asking the student's name, stored in localStorage.
2. Hero: big question box "What didn't you get?" and 3 example chips from /api/examples.
3. While waiting for /api/ask (takes a few seconds): skeleton loader with a line like "Searching 1 lecture…".
4. Found: video player that plays only the snippet. YouTube: YouTube IFrame Player API with start/end, pause at end. File: <video> with currentTime, stop on timeupdate. Custom progress bar for just the clip and a "Replay clip" button. Below: concept, "Lecture N · mm:ss–mm:ss", the 2-line summary, a badge "Your professor's own words".
5. Homework flag: still show the clip, with "Here's where the method is explained. Try it yourself, then check below."
6. Not covered: friendly card "Your professor hasn't covered this in the recorded lectures yet. We've sent your question to them."
7. "Check yourself" card after the clip: one question at a time, option buttons, instant feedback via /api/answer with the explanation. Wrong: offer the first alternative clip ("See it explained another way"), then question 2.
8. Side panel "Rewatch before the exam" from /api/students/{id}/rewatch.

lecturer.html:
1. Metric cards from /api/lecturer/stats totals.
2. "Where students get stuck": snippets ranked by times asked, fail-rate bar, red flag over 40% "possibly unclear", click plays the clip in a modal.
3. "Questions your lectures don't cover" with Resolve buttons.
4. Knowledge graph with vis-network (vendored): node size = times asked, colour green→red by fail rate, click a snippet node to play it.
5. Small "Load demo data" button in the footer (/api/lecturer/seed).
Shared header with links between the two pages.

Check it: serve web/ with `python -m http.server 5500 -d web` and open http://localhost:5500/?mock=1. If Playwright is available, take screenshots of every state into web/screenshots/ (add a .gitignore with "*" inside that folder), look at them and fix anything off. Once api.py exists, test against http://localhost:8000 too.
Finish: notes in NOTES-web.md (what's mocked, anything the API must change), stop.
```

## T4 (restart as Claude Code, Opus) – real lectures and snippet quality

Needs `.env` in Cache-Me-Outside first. Restart T4 so it runs on Opus: `/exit`, then `claude --model opus --dangerously-skip-permissions`.

```
RULES: All agents work in this one folder (Cache-Me-Outside). There are no worktrees and no commits. Do NOT run any git command. Only edit the files listed as yours. If you need something changed in another file, write it in your NOTES file. Run only your own tests. Python env is the active venv; install packages with pip, append new ones to requirements.txt.

Yours: rewind/snippet_maker.py (only SNIPPET_PROMPT and post-processing tweaks), data/lectures/ (new folders only, don't edit demo01), tests/test_snippet_maker.py, NOTES-lectures.md.

Goal: real lecture data for the demo, with snippets good enough to show a jury.
1. Check .env works: python -c "from rewind import llm; print(llm.ask_json('Reply with JSON {\"ok\": true}', 'ping', 50))"
2. Find 2 English university lectures on YouTube, 30–60 min, with captions, on topics a jury understands (e.g. intro economics, intro psychology, intro physics). MIT OpenCourseWare or Yale Open Courses are good sources. Run:
   python -m rewind.snippet_maker --youtube <URL> --lecture-id <short-id> --title "..." --course "..." --lecture-number N
3. Review the output critically as if you were the student and the professor:
   - Do cuts start and end on whole thoughts, not mid-sentence?
   - Is each concept something a student would actually search for?
   - Are summaries only what the prof said?
   - Do questions test understanding and have one clearly right answer?
   Tune SNIPPET_PROMPT and rerun until they're good. Keep the tests passing.
4. Write 6 demo questions per lecture in NOTES-lectures.md: 4 a student would ask in their own words (they should find the right snippet), 1 related but not covered, 1 homework-style.
5. Once rewind/search.py is complete (T2), run python -c "from rewind import search; search.index_all()" and try your 12 questions with search.answer. Record which snippet each returned and whether it was right.
Finish: notes in NOTES-lectures.md, stop.
```

## T2 (Codex) – let it finish

Then paste:

```
RULES: All agents work in this one folder (Cache-Me-Outside). There are no worktrees and no commits. Do NOT run any git command. Only edit your own files (rewind/search.py, tools/eval_search.py, tests/test_search.py, tests/search_eval.json, NOTES-search.md). Run only your own tests.

Note for later: store.log_ask and app.py rely on answer() returning "best" as a plain snippet dict, as you have it now. Keep that shape. When you're done, write NOTES-search.md with the eval numbers and stop.
```

## After all four finish – integration (any terminal, Claude Code, Opus)

```
Read AGENTS.md, docs/round2-prompts.md and all NOTES-*.md files. All modules were built by parallel agents in this one folder. Nothing is committed yet (the repo has no commits).
1. Git setup: run git worktree prune. On main, commit only AGENTS.md, CLAUDE.md, README.md, .gitignore, .env.example and docs/ ("Add project context for coding agents"), push main. Then create branch rewind from main.
2. Make sure .gitignore excludes __pycache__/, .pytest_cache/, web/screenshots/, data/rewind.db, embeddings and video files. Never commit .env.
3. Check every module matches the shapes the others expect (api.py and app.py call search, store and quiz; web/ calls api.py per the contract). Fix mismatches. Fake mode only as a fallback.
4. pip install -r requirements.txt, run the full pytest, fix failures.
5. Start the website (uvicorn api:app --port 8000) and run the demo questions from NOTES-lectures.md. Then: fail a quiz question, and check the lecturer page after "Load demo data". Use Playwright screenshots to check every state. Fix anything broken or ugly. Streamlit (app.py) stays as backup.
6. Write DEMO.md: setup in 5 commands, the demo script with the questions that work best, known limits.
7. Commit on rewind in logical commits (shared modules, snippet maker, search, store and API, Streamlit app, website, lecture data, demo docs) and push rewind. Delete the NOTES files in the last commit.
```
