# AGENTS.md – read this first

Context for every coding agent (Claude Code, Codex, Copilot, Cursor, Gemini) working in this repo.

## The project: Rewind

Students ask a question and get the exact 45–120 seconds of their professor's lecture that answers it, plus a 2-line summary, then 1–2 check questions. If no lecture covers the question, the app says so and flags it to the lecturer. A lecturer view shows which snippets are asked about most and which questions students fail.

The answer is always the professor's own words. The AI only finds the right moment, summarises it and writes the check questions. It never solves homework.

More background: `docs/concept.md`. Task prompts for each module: `docs/agent-prompts.md`.

## Branches and parallel work

- `main` holds context and docs only. Don't push code straight to `main`.
- `rewind` is the project branch. Each module has its own branch off `rewind` (`rewind/snippet-maker`, `rewind/search`, `rewind/store`, `rewind/app`), checked out in its own git worktree folder next to this repo, so several agents can work at once without touching each other's files.
- **Only edit the files your task owns** (listed in your prompt). If you need a change in someone else's file, write it in `NOTES-<your-branch-name>.md` at the repo root instead of editing it.
- `requirements.txt`: only append lines, never reorder or remove.
- Code against the function signatures in the `rewind/` stubs. Don't change a signature; if you must, say so in your NOTES file.
- When done: run `pytest`, commit, and push your branch. The integration step merges everything into `rewind`.

## Rules

- Python 3.11. Keep dependencies minimal and list them in `requirements.txt`.
- Never cut or re-encode video. A snippet is only a start/end time; the player jumps to it.
- LLM provider is Anthropic. Read `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` from the environment (`.env` via python-dotenv, see `.env.example`). Never hardcode model names or keys.
- Lectures may be in Italian or English. Summaries and questions must be in the lecture's language. Use multilingual models where language matters.
- Each module is importable and testable on its own. No global state besides config.
- Start each module with a short docstring explaining inputs and outputs.
- Don't commit `.env`, lecture videos, `data/rewind.db` or `embeddings.npy`.

## Folder layout

```
app.py                         # Streamlit app, student view
pages/lecturer.py              # Streamlit lecturer view
rewind/
  config.py                    # paths, thresholds, env loading
  lectures.py                  # load lectures and snippets from data/
  transcript_io.py             # load/convert transcripts (teammate's transcriber plugs in here)
  snippet_maker.py             # transcript -> snippets.json
  search.py                    # question -> best snippet or "not covered"
  store.py                     # SQLite: asks, quiz attempts, gaps
  quiz.py                      # grading, homework detection
data/lectures/<lecture_id>/
  meta.json                    # {"lecture_id", "title", "course", "lecture_number", "video_url"}
  transcript.json
  snippets.json
  embeddings.npy               # created by search.py, not committed
data/rewind.db                 # created by store.py, not committed
tests/
tools/                         # review_snippets.py, eval_search.py
```

## Data contracts (don't change without telling the team)

`transcript.json`, produced by the transcriber or youtube-transcript-api. Times in seconds.

```json
[
  {"start": 12.4, "end": 18.9, "text": "Today we look at supply and demand..."},
  {"start": 18.9, "end": 25.1, "text": "First, what is a demand curve..."}
]
```

`snippets.json`, produced by `rewind/snippet_maker.py`.

```json
[
  {
    "id": "econ01-003",
    "lecture_id": "econ01",
    "concept": "Demand curve",
    "start": 18.9,
    "end": 104.0,
    "summary": "Two plain sentences using only what the professor said.",
    "text": "Full transcript text of this snippet",
    "questions": [
      {
        "q": "Question text",
        "options": ["A", "B", "C", "D"],
        "answer": 1,
        "explanation": "Why that option is right, based on the lecture."
      }
    ]
  }
]
```

## Test fixture

Until real data exists, use `data/lectures/demo01/`: a `meta.json` pointing to a public YouTube lecture, a short `transcript.json`, and a hand-written `snippets.json` with 3 snippets following the contract. It's created in the setup step, together with the stubs in `rewind/`.
