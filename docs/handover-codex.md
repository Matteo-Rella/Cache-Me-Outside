# Rewind: Claude Code to Codex handover

Inspected 14 November 2026. This describes observed files, not just the older README or previous chats.

## Checkout and preservation

- Repository: `C:\Users\User\Documents\GitHub\Cache-Me-Outside`.
- Branch: `rewind`, HEAD `92a80b1` (`Add agent notes`); local tracking ref `origin/rewind` matches. No fetch or push was performed.
- Origin: `https://github.com/Matteo-Rella/Cache-Me-Outside.git`. GitHub open-issue query returned an empty list.
- One registered Git worktree. At takeover: 27 modified tracked files and 11 untracked entries (some are folders), with no staged changes. Tracked diff: 4,215 insertions and 281 deletions. The untracked context-package, settings and chat files are real work and must be preserved.
- Preservation folder: `C:\Users\User\Documents\ChatGPT\AI Education project\rewind-handover`. It contains the original status, tracked/staged patches, a source backup, an isolated test copy and test results. Secrets, databases, source media and generated embedding caches are deliberately excluded from the source backup; they remain in the original checkout.
- No original source edits were reverted, committed or pushed. The takeover adds only this document and `tools/start_codex.ps1`.

## What exists

| Area | Observed implementation |
| --- | --- |
| Main interface | Static HTML/JavaScript in `web/`, served by `api.py` (FastAPI). Student, lecturer, QR and local-only AI settings pages. `app.py` is a separate Streamlit interface. |
| Lecture input | `rewind/transcript_io.py` normalizes timestamped transcripts; `rewind/ingest.py` accepts `--transcript` to bypass transcription. Keep the teammate's transcriber separate. |
| Snippets | `rewind/snippet_maker.py`: transcript-grounded concepts, summaries, questions, timestamp post-processing, answer balancing and batched answer verification/repair. Verification failures can leave questions marked unchecked; this is not professor approval. |
| Retrieval | `rewind/search.py`: multilingual embeddings plus keyword ranking, optional LLM coverage decisions, alternatives and not-covered results. Existing evaluation notes explain related-topic false positives without the LLM check. |
| Learning loop | Quiz grading, homework classification, SQLite asks/attempts/gaps, lecturer statistics and chat-message storage. |
| AI providers | `rewind/llm.py` and `rewind/settings.py` already support Anthropic, OpenAI, Gemini and OpenAI-compatible services. Local settings override environment defaults. Changing the coding assistant does not change the app's provider. |
| No-key preprocessing | `rewind/context_package.py`: multipart paste-ready prompts, portable ZIP package, JSON extraction/validation, repair messages and import CLI. |
| Content | `demo01` (hidden fixture), `econ01`, `psych02`; `econ01-chat` has metadata/transcript but no imported snippets. No external chat answer file was supplied or imported during takeover. |
| Review | `tools/review_snippets.py` records quality notes in `review_notes.json`. Student retrieval does not enforce an approved publication state. |
| Partial Round 5 | Chat/slide web components, API endpoints, chat storage and `rewind/chat.py` exist. Their required LLM/slide foundations are incomplete. |

## Verified baseline and limits

- Existing sibling environment: `..\cmo-venv\Scripts\python.exe`, Python **3.15.2**, despite `AGENTS.md` specifying Python 3.11. Do not rebuild or replace it during takeover; choose and document a reproducible version in a separate task.
- **327 existing tests passed in 5.96 seconds**, on an isolated copy using this environment. Dummy credentials, temporary runtime data and offline model settings were used. No live AI call was requested. The restricted Windows run stalled on the first API test; a normal-access run passed, including the single health-test diagnostic.
- This verifies the existing suite, not live provider output quality or visual completeness. There is no `tests/test_chat.py`, `tests/test_slides.py`, `tools/eval_chat.py` or `tests/chat_eval.json` in the inspected checkout.
- Installed dependency metadata reports `pyvis` and `google-genai` missing. Do not reinstall the whole environment blindly. `rewind/slides.py` and its PDF/PPTX requirements are not implemented yet.
- Running-process inspection found Windows Terminal with three Claude CLI child sessions and an existing Codex CLI session; a separate Claude desktop process also exists. Their current directories could not be established from their command lines. They were not stopped or re-tasked.
- A Python static server uses the sibling environment and listens on port **5500**. No Uvicorn or Streamlit listener was found in the inspected process/port checks. `web/js/api.js` falls back to browser mocks when API health is unreachable, so a working page on a static server does not establish that the real backend works.

## Continue with Codex

Codex is already installed and this checkout already contains `AGENTS.md`. The official [Codex instructions documentation](https://learn.chatgpt.com/docs/agent-configuration/agents-md) describes how those repository instructions are read. `CLAUDE.md` only points back to that shared file; no source-code conversion is needed.

From a PowerShell terminal in the repository:

```powershell
.\tools\start_codex.ps1
```

This opens one coordinator session in the existing checkout, prepends the sibling Python environment to PATH and uses ordinary workspace permissions/approval handling. It does not launch additional agents, modify the app's AI provider or terminate existing terminals.

To replace a specific old task session, first finish/cancel its active operation and exit that Claude CLI normally, then run one matching role in that terminal:

```powershell
.\tools\start_codex.ps1 -Role T1  # web interface
.\tools\start_codex.ps1 -Role T2  # LLM, settings, search, slides
.\tools\start_codex.ps1 -Role T3  # API, store, ingest
.\tools\start_codex.ps1 -Role T4  # snippet quality, packages, chat
```

These are alternatives for explicitly chosen sessions; do not run every command automatically. The old chat histories stay with Claude; repository files, NOTES and this handover supply the Codex context. `-PrintOnly` checks the launch configuration without starting a session. Do not start a coordinator editing the same files while a role session is working on them.

Historical docs conflict: `AGENTS.md` describes worktrees and automatic commit/push, while later prompts describe one shared folder and no commits. The observed checkout has one worktree and substantial local work. Continue in place, respect task ownership, and leave staging/commit/push decisions out of the takeover. Preserve the data contracts and append-only dependency convention. Historical live-call budgets and bypass-permission launcher examples are not fresh authorization.

## Next concrete implementation steps

1. **Finish Round 5 prerequisites.** Add provider streaming `llm.chat`, optional `purpose` selection for runtime/preprocessing, `llm.estimate(requests)`, and settings support for `chat_policy` and `preprocess_model`. Currently `llm.chat`/`estimate` are absent and the settings field allowlist excludes the new fields. `api.chat_status()` therefore disables real chat. Verify all providers with mocks before connecting the existing chat interface.
2. **Complete slides and integration.** Implement `rewind/slides.py`, PDF/PPTX ingestion and alignment, slide retrieval/`slide_only`, and the missing slide tests. Add chat tests for streamed citation validation, unknown material, homework policy, malformed streams and stored history. Retest the existing API/web pieces against actual foundations rather than mocks alone. Repair AGENTS/README contracts to match the final implementation.
3. **Make professor approval real.** Reuse the quality-review tool as a starting point, but introduce a versioned draft/review/approved state for snippets and exercises, reviewer provenance, approval invalidation after edits, and approved-only student retrieval/quiz/chat context. Test that unapproved or changed content cannot be presented as approved. Do not label today's generated content professor-approved.
4. **Close the teammate/demo loop.** Validate one teammate-produced transcript through `transcript_io`, then process one real lecture, review summaries/questions, index it and demonstrate question -> timed clip -> quiz -> follow-up -> lecturer gap. Test one genuinely external chat response through the package importer. Keep generation calls explicitly scoped and cost-capped.

## Useful existing entry points

```powershell
# API + website (real mode; can use the configured AI on student requests)
..\cmo-venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000

# Explicit fake backend for an interface demo
$env:REWIND_FAKE = '1'
..\cmo-venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
# In a reused shell, remove REWIND_FAKE before returning to real mode.

# Export prompts/package without an AI API call
..\cmo-venv\Scripts\python.exe -m rewind.context_package build econ01 econ01-package.zip
..\cmo-venv\Scripts\python.exe -m rewind.context_package prompt econ01 1

# Import only when a complete answer file actually exists (writes that lecture's snippets)
..\cmo-venv\Scripts\python.exe -m rewind.context_package import econ01-chat C:\path\to\chatgpt-answer.txt
```

For the main app, visit `http://127.0.0.1:8000/`, then `lecturer.html` or `settings.html`. API settings/package/generation endpoints are local-only. No server was started and no imported lecture was replaced during this inspection.
