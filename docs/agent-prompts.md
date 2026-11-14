# Build plan: 4 terminals

Every agent reads `AGENTS.md` automatically (Claude Code through `CLAUDE.md`, Codex reads `AGENTS.md` directly), so these prompts only describe the task.

| Step | Terminal | Tool and model | Task | Rough time |
|---|---|---|---|---|
| 0 | T1 | Claude Code, Opus | Setup: stubs, fixture, worktrees | 15 min |
| 1 | T1 | Claude Code, Opus | Snippet maker + review tool | 45 min |
| 1 | T2 | Codex, default model, high reasoning | Search + gap detection + eval | 45 min |
| 1 | T3 | Claude Code, Sonnet | Database + lecturer view + knowledge graph | 45 min |
| 1 | T4 | Claude Code, Sonnet | Student app + quiz | 45 min |
| 2 | T1 | Claude Code, Opus | Merge, run on a real lecture, fix, polish for demo | 30–60 min |

Why these models: the snippet maker's prompt quality decides the whole demo, and integration means fixing other agents' bugs, so both get Opus. Search is a self-contained algorithm task, a good fit for Codex and a second opinion on the code. The database and the two Streamlit pages are routine work where Sonnet is fast and good.

## Before you start (once)

1. In GitHub Desktop, make sure you're on the `rewind` branch and it's pushed.
2. Create a file called `.env` in the repo folder with:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ANTHROPIC_MODEL=<model id>
   ```
   For the model id, copy a current Sonnet model id from docs.claude.com/en/docs/about-claude/models. Sonnet is enough for the app's own LLM calls and keeps API costs low.
3. Open a terminal in the repo folder and start T1:
   ```
   cd $HOME\Documents\GitHub\Cache-Me-Outside
   claude --model opus
   ```

---

## Step 0 – Setup (T1, Claude Code, Opus)

```
Read AGENTS.md. You are doing the setup step on the `rewind` branch so four agents can work in parallel afterwards. We're on Windows, use PowerShell syntax for any shell commands.

1. Check out `rewind` (create it from main if missing), pull, and merge main into it so it has the latest AGENTS.md and docs.
2. Create requirements.txt with: anthropic, python-dotenv, streamlit, sentence-transformers, numpy, youtube-transcript-api, networkx, pyvis, pytest. Install it with pip into the active virtual environment (check `python -c "import sys; print(sys.prefix)"` points to cmo-venv; don't create a new venv).
3. Create the package with working code for the small shared parts and stubs for the rest:
   - rewind/__init__.py
   - rewind/config.py (WORKING): load .env with python-dotenv; DATA_DIR, LECTURES_DIR, DB_PATH (Path objects relative to repo root); SIMILARITY_THRESHOLD = 0.35; EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"; get_model() reads ANTHROPIC_MODEL and errors clearly if missing.
   - rewind/llm.py (WORKING): ask_json(system, user, max_tokens) -> parsed JSON. Calls Anthropic, strips code fences, retries once on invalid JSON with the error in the message.
   - rewind/lectures.py (WORKING): list_lectures(), load_meta(lecture_id), load_transcript(lecture_id), load_snippets(lecture_id), all_snippets(), get_snippet(snippet_id), save_snippets(lecture_id, snippets).
   - rewind/transcript_io.py (WORKING): load_transcript(path) with validation (sorted, start < end, non-empty text); from_whisper(result) for openai-whisper and faster-whisper output; from_youtube(video_id) via youtube-transcript-api (check the installed version's API, it changed in v1.x); merge_short_segments(segments, min_seconds=3).
   - rewind/snippet_maker.py (STUB): make_snippets(transcript, lecture_id) -> list[dict]; main() CLI.
   - rewind/search.py (STUB): index_lecture(lecture_id), index_all(), search(question, top_k=3) -> list[{"snippet", "score"}], answer(question) -> {"status": "found"|"not_covered", "best", "alternatives", "score"}.
   - rewind/store.py (STUB): init_db(), log_ask(student_id, question, result), log_attempt(student_id, snippet_id, question_index, chosen, correct), flag_gap(student_id, question), student_weak_spots(student_id) -> list[snippet_id], lecturer_stats(lecture_id=None) -> list[dict], open_gaps() -> list[dict], resolve_gap(gap_id), seed_demo_data().
   - rewind/quiz.py (STUB): grade(snippet, question_index, chosen) -> {"correct": bool, "explanation": str}; is_homework(question) -> bool.
   Every stub has a docstring with inputs, outputs and the exact return shape, then raises NotImplementedError.
4. Fixture: data/lectures/demo01/ with meta.json (a real public YouTube economics or physics lecture, about 10+ minutes), transcript.json (about 3 minutes of realistic segments) and snippets.json with 3 snippets that follow the contract exactly, including 2 questions each.
5. tests/test_transcript_io.py and tests/test_lectures.py for the working modules. Run pytest, make it pass.
6. Commit "Scaffold: shared modules, stubs, demo fixture" and push `rewind`.
7. Create four worktrees next to the repo, each on a new branch from `rewind`, and copy .env into each:
   ..\cmo-snippets  -> rewind/snippet-maker
   ..\cmo-search    -> rewind/search
   ..\cmo-store     -> rewind/store
   ..\cmo-app       -> rewind/app
8. Print the four folder paths and stop.
```

Then open the terminals with the commands in "Terminal commands" at the end of this file.

---

## Step 1, T1 – Snippet maker (Claude Code, Opus, folder cmo-snippets)

```
Read AGENTS.md. You're on branch rewind/snippet-maker. You own: rewind/snippet_maker.py, tools/review_snippets.py, tests/test_snippet_maker.py, data/lectures/ (new lectures only, don't edit demo01).

Implement make_snippets and the CLI:
  python -m rewind.snippet_maker --youtube <URL> --lecture-id econ01 [--title "..."] [--course "..."] [--lecture-number 1]
  python -m rewind.snippet_maker --transcript <path> --video-url <URL> --lecture-id econ01
Use rewind.transcript_io, rewind.lectures and rewind.llm.ask_json; don't reimplement them.

Steps:
1. Format the transcript for the LLM as "[123.4] text" lines. If over ~100k characters, split into overlapping windows and merge.
2. Keep the LLM instructions in a constant SNIPPET_PROMPT so we can tune it. Rules:
   - Cut where the topic changes. Each snippet 45–120 s, never over 150.
   - Skip admin talk (attendance, breaks, exam logistics).
   - concept: short noun phrase a student would search for.
   - summary: exactly 2 sentences, only what the professor said, no outside knowledge.
   - 2 multiple-choice questions, 4 options, testing understanding (apply or explain), not word-for-word recall, each with an explanation grounded in the lecture.
   - Same language as the lecture.
3. Fix the output in code: snap start/end to transcript segment boundaries, sort, remove overlaps, drop under 20 s, warn over 150 s, validate questions (4 options, valid answer index), fill "text" from the transcript, assign ids "<lecture_id>-<3 digits>".
4. Save with lectures.save_snippets and print a table: id, concept, mm:ss–mm:ss, duration.

Also tools/review_snippets.py: Streamlit page to pick a lecture, list snippets, play each with st.video(start_time=...), and save a note per snippet to data/lectures/<id>/review_notes.json.

Tests with the LLM mocked: snapping, overlaps, validation, ids.

Then run it for real on 2 YouTube lectures with English or Italian captions (pick 30–60 min university lectures). Look at the output, adjust SNIPPET_PROMPT if cuts or questions are poor, rerun. Commit the generated lecture folders.
Finish: pytest passes, commit, push rewind/snippet-maker. Write what you tuned and any open issues in NOTES-snippet-maker.md.
```

---

## Step 1, T2 – Search and gap detection (Codex, folder cmo-search)

```
Read AGENTS.md. You're on branch rewind/search. You own: rewind/search.py, tools/eval_search.py, tests/test_search.py, tests/search_eval.json.

Implement the functions stubbed in rewind/search.py, keeping their signatures and return shapes.
1. index_lecture / index_all: embed each snippet with sentence-transformers (config.EMBEDDING_MODEL). Text = concept + summary + text. Save embeddings.npy and an ids file next to snippets.json. Re-index automatically if snippets.json is newer than the embeddings.
2. search: cosine similarity over all lectures. Load the model and embeddings once and cache them.
3. answer: if top score < config.SIMILARITY_THRESHOLD -> not_covered. Otherwise ask the LLM (rewind.llm.ask_json) whether any of the top 3 excerpts actually answers the question: return {"best_index": int or null, "reason": str}. null -> not_covered. Put the prompt in a constant. Use the result to choose "best"; the rest become "alternatives".
4. tools/eval_search.py: reads tests/search_eval.json ([{question, expected_snippet_id or null}]), prints hit@1, hit@3, and not_covered precision/recall, with and without the LLM check. Write 12 items for demo01: 8 answerable (paraphrased, not copied from summaries, at least 2 in Italian), 4 not covered (2 off-topic, 2 on-topic but not explained in the fixture).
5. Tests with the LLM mocked: obvious question finds the right snippet; unrelated question is not_covered; the LLM returning null gives not_covered.
Finish: pytest passes, run the eval and put the numbers in NOTES-search.md, commit, push rewind/search.
```

---

## Step 1, T3 – Database and lecturer view (Claude Code, Sonnet, folder cmo-store)

```
Read AGENTS.md. You're on branch rewind/store. You own: rewind/store.py, pages/lecturer.py, tests/test_store.py.

Part 1: rewind/store.py with sqlite3 (standard library, no ORM), DB at config.DB_PATH, created on first use. Keep the stub signatures.
Tables:
- asks(id, student_id, question, snippet_id NULL, status, score, created_at)
- attempts(id, student_id, snippet_id, question_index, chosen, correct, created_at)
- gaps(id, question, student_id, created_at, resolved INTEGER DEFAULT 0)
Notes:
- student_weak_spots: snippets where the student's latest attempt on any question was wrong, newest first.
- lecturer_stats: per snippet: concept, lecture_id, start, times_asked, attempts, fail_rate.
- open_gaps: unresolved, grouped by lowercase question text with a count.
- seed_demo_data: realistic fake activity on demo01 (and any other lectures present): ~10 students, ~60 asks, ~100 attempts, 1 snippet with a clearly high fail rate, 5 gap questions. Idempotent: clears previous demo rows first.
Tests use a temporary DB path.

Part 2: pages/lecturer.py (Streamlit page, part of the app T4 is building):
1. Top metrics: questions asked, % answered by a snippet, % not covered, average fail rate.
2. "Most asked moments" table: concept, lecture, link to the timestamp, times asked, fail rate. Flag fail rate over 40% as "possibly unclear in lecture".
3. Bar chart of fail rate per snippet.
4. "Questions the lectures don't cover" with a "Mark resolved" button per row.
5. Knowledge graph with networkx + pyvis in st.components.v1.html: course → lecture → snippet → concept. Snippet node size = times asked, colour green→red by fail rate. Same concept in several lectures = one shared node.
6. A "Load demo data" button calling seed_demo_data().
Check it renders with: streamlit run pages/lecturer.py
Finish: pytest passes, commit, push rewind/store. Notes in NOTES-store.md.
```

---

## Step 1, T4 – Student app (Claude Code, Sonnet, folder cmo-app)

```
Read AGENTS.md. You're on branch rewind/app. You own: app.py, rewind/quiz.py, tests/test_quiz.py, tests/fakes.py, .streamlit/config.toml.

search.py and store.py are being built in parallel. Until they're merged, use tests/fakes.py: a fake answer() that does keyword matching on demo01 snippets, and an in-memory fake store with the same function names. Switch with env var REWIND_FAKE=1. Don't edit search.py or store.py.

Part 1: rewind/quiz.py
- grade(snippet, question_index, chosen) -> {"correct", "explanation"}.
- is_homework(question) -> bool, using rewind.llm.ask_json, with a cheap keyword pre-check ("solve", "calculate", "risolvi", "calcola", "esercizio", numbered problems) so obvious cases skip the API.

Part 2: app.py, the student page of a multipage Streamlit app (pages/lecturer.py comes from another branch).
1. Sidebar: student name (session_state, used as student_id), course/lecture filter, and a "Rewatch before the exam" list from store.student_weak_spots with a button per snippet.
2. Main: title "Rewind", text box "Ask about the lecture".
3. On submit: search.answer, store.log_ask.
   - found: concept, lecture title and number, mm:ss–mm:ss, the 2-line summary, then st.video(video_url, start_time=..., end_time=...) (check the installed Streamlit supports end_time for YouTube; if not, start_time only and show the end time as text).
   - not_covered: "This isn't covered in the recorded lectures. I've sent your question to the lecturer." and store.flag_gap.
   - If is_homework: still show the clip, with "Here's where the prof explains the method. Try it yourself, then check with the questions below." Never write a solution.
4. "Check yourself": question 1 as radio + submit, logged with store.log_attempt. Correct: short praise + explanation. Wrong: explanation, then the next alternative snippet ("Watch it explained another way"), then question 2.
5. All state in st.session_state so clicks don't reset the quiz. Clean single column, no debug output. Light theme in .streamlit/config.toml.
Check it with: $env:REWIND_FAKE="1"; streamlit run app.py
Finish: pytest passes, commit, push rewind/app. Notes in NOTES-app.md.
```

---

## Step 2 – Integration (T1, Claude Code, Opus, main repo folder)

Start once all four branches are pushed. Close T2–T4.

```
Read AGENTS.md and all NOTES-*.md files on the four branches. You're in the main repo folder.
1. Check out rewind, pull, then merge rewind/snippet-maker, rewind/search, rewind/store, rewind/app one at a time. Resolve conflicts (requirements.txt: keep all lines, dedupe).
2. Remove the REWIND_FAKE path from app.py, or keep it only as a fallback that is off by default.
3. pip install -r requirements.txt, run pytest, fix failures.
4. Run search.index_all() and the search eval on the real lectures from the snippet-maker branch; write 10 eval questions for one real lecture and tune SIMILARITY_THRESHOLD.
5. Run streamlit run app.py and go through the demo script: ask 3 good questions, 1 off-topic, 1 homework-style; fail a quiz question; check the lecturer page after "Load demo data". Fix anything broken or ugly.
6. Write DEMO.md: setup in 5 commands, the demo script with the exact questions that work well, known limits.
7. Commit, push rewind. Delete the NOTES files and remove the worktrees (git worktree remove).
```

When it's done, open a pull request from `rewind` into `main` in GitHub Desktop (Branch → Create pull request), or keep `rewind` as the demo branch.

---

## Terminal commands (PowerShell, permissions off)

All four terminals share one Python environment at `Documents\GitHub\cmo-venv`, and agents run with no permission prompts. Only do this on your own machine, and keep the agents inside these folders.

**Once, before anything:**

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
cd $HOME\Documents\GitHub
python -m venv cmo-venv
```

**Step 0, T1 (main repo):**

```
cd $HOME\Documents\GitHub\Cache-Me-Outside
..\cmo-venv\Scripts\Activate.ps1
claude --model opus --dangerously-skip-permissions
```

**Step 1, after step 0 printed the four folders.** Exit T1 (`/exit`) and open the other three.

T1, snippet maker:
```
cd $HOME\Documents\GitHub\cmo-snippets
..\cmo-venv\Scripts\Activate.ps1
claude --model opus --dangerously-skip-permissions
```

T2, search:
```
cd $HOME\Documents\GitHub\cmo-search
..\cmo-venv\Scripts\Activate.ps1
codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=high
```

T3, database and lecturer view:
```
cd $HOME\Documents\GitHub\cmo-store
..\cmo-venv\Scripts\Activate.ps1
claude --model sonnet --dangerously-skip-permissions
```

T4, student app:
```
cd $HOME\Documents\GitHub\cmo-app
..\cmo-venv\Scripts\Activate.ps1
claude --model sonnet --dangerously-skip-permissions
```

**Step 2, integration (T1, after all four branches are pushed):**
```
cd $HOME\Documents\GitHub\Cache-Me-Outside
..\cmo-venv\Scripts\Activate.ps1
claude --model opus --dangerously-skip-permissions
```
