# Round 4 prompts: bring your own AI

Start after all round 3 terminals have finished (this touches the same files).

## The feature

An "AI settings" page (local admin only) where the lecturer chooses how Rewind gets its AI:

1. **API**: Anthropic, OpenAI, Gemini, or any OpenAI-compatible server (a base URL: covers Ollama and LM Studio running locally, OpenRouter, Groq, a university's own server). Pick provider, model, paste key, "Test connection".
2. **Context package** (no API needed): download a package for a lecture, give it to any AI chat (ChatGPT, Gemini, Claude) or coding agent (Claude Code, Codex, Gemini CLI), paste or upload the answer back. Rewind validates it and, if something's wrong, gives an error message written so you can paste it straight back to the AI.
3. **None**: everything works except making new snippets; search uses the similarity threshold only, homework detection uses keywords only.

Settings live in data/settings.json (gitignored), override .env, and the key is never sent back to the browser in full (masked like "sk-…a1b2").

**Security:** when the server runs on 0.0.0.0 for phones, students can reach it. Every settings, import and generate endpoint, and settings.html itself, must only answer requests from 127.0.0.1/::1 (403 otherwise).

RULES (all terminals): one shared folder (Cache-Me-Outside), no worktrees, no commits. Do NOT run any git command. Only edit the files listed as yours; anything else goes in your NOTES file. Run only your own tests. Never kill processes you didn't start.

## Contract (shared by all four)

Python:
- rewind/settings.py (T2): load() -> dict, save(dict), public() -> dict with key masked. Fields: mode ("api" | "package" | "none"), provider ("anthropic" | "openai" | "gemini" | "openai_compatible"), model, api_key, base_url. Precedence: settings.json > .env > defaults.
- rewind/llm.py (T2): ask_json reads rewind.settings; available() true only in api mode with key and model (base_url instead of key is fine for openai_compatible); test_connection() -> {ok, message, latency_ms}.
- rewind/context_package.py (T4): build_package(lecture_id) -> bytes (zip); build_chat_prompt(lecture_id, part=None) -> list[str] (one paste-ready message per part); import_answer(lecture_id, raw_text) -> {ok, snippets_count, errors: [str], fix_message: str}. raw_text may be JSON or a chat reply with prose and code fences around the JSON.

HTTP (T3), all local-only except where noted:
- GET  /api/settings -> settings.public()
- POST /api/settings {mode, provider, model, api_key?, base_url?} -> public settings (api_key omitted = keep current)
- POST /api/settings/test -> llm.test_connection()
- GET  /api/lectures/{id}/package -> zip download
- GET  /api/lectures/{id}/prompt -> {parts: [str]}
- POST /api/lectures/{id}/import {raw_text} -> import_answer result; on ok, reindex the lecture
- POST /api/lectures/{id}/generate -> runs the snippet maker with the API; {ok, snippets_count, error}
- GET  /api/ai-status (public) -> {mode, provider, can_generate, coverage_check: bool} so the student page can show nothing broken

---

## T2 (Codex) – settings + more providers

Yours: rewind/settings.py, rewind/llm.py, rewind/config.py, .env.example, tests/test_settings.py, tests/test_llm.py, NOTES-search.md.

Implement the Python contract above for settings.py and llm.py. Add the openai_compatible provider using the OpenAI SDK with base_url (key optional, e.g. "ollama"). test_connection sends a tiny JSON request and reports a clear human message on failure (bad key, unknown model, server unreachable). Keep ask_json's signature. If a provider has a small context window, expose llm.max_input_chars() (sensible defaults per provider, overridable in settings) so the snippet maker can split transcripts. data/settings.json goes in your notes for .gitignore. Tests with mocked SDKs. Stop.

## T4 (Claude Code, Opus) – the context package

Yours: rewind/context_package.py, rewind/snippet_maker.py (only to reuse/expose functions), tests/test_context_package.py, NOTES-lectures.md.

Build the package so a non-technical lecturer succeeds on the first try with any AI chat, and a coding agent can do it unattended.
The zip contains:
- START-HERE.md: 5 short steps for a chat AI (open ChatGPT/Gemini/Claude, paste PROMPT-part-1.md, ... , save the reply, upload it in Rewind), plus one line for coding agents ("open AGENT.md").
- PROMPT-part-N.md: paste-ready messages. Each has the full instructions (SNIPPET_PROMPT), the output JSON schema with one short example, and its slice of the transcript as "[123.4] text" lines. Split so each part stays under ~60k characters (chat limits); later parts say "continue from part N-1, output only snippets for this slice". Every part asks for JSON only inside one ```json block.
- AGENT.md: for Claude Code / Codex / Gemini CLI: read transcript.json and the instructions, write raw_llm_output.json (merge all parts), then run the import command, fix errors until it passes.
- transcript.json, schema.json, meta.json.
import_answer: extract the JSON from messy chat replies (code fences, prose before/after, several parts pasted together), merge parts, run the snippet maker's normal post-processing, save. On failure return errors plus fix_message: a ready-to-paste message for the AI ("Your answer had these problems: … Send the corrected JSON only.").
Test end to end with econ01: build the package, act as the chat AI yourself using only PROMPT-part files, import, and check the result matches the quality of round 3. Also test import with a deliberately broken reply. Stop.

## T3 (Claude Code, Sonnet) – API endpoints + local-only guard

Yours: api.py, tests/test_api.py, NOTES-store.md.

Add the HTTP contract above, wrapping rewind.settings, rewind.llm and rewind.context_package (other agents build them in parallel; if a function is missing, return 501 with a clear message rather than crash). Add a local-only dependency that returns 403 for non-loopback clients and apply it to every settings/package/import/generate endpoint and to serving settings.html. Generate runs in a background thread with a simple status endpoint GET /api/lectures/{id}/generate/status ({state: idle|running|done|error, message}) since it can take a minute. Tests: local-only guard (simulate a non-loopback client), masked key never returned, import ok and error paths with a mocked context_package. Stop.

## T1 (Claude Code, Opus) – AI settings page

Yours: web/, NOTES-web.md.

web/settings.html, linked from the lecturer page header ("AI settings"), same design as the rest.
1. Three cards to choose the mode: "Use an API", "Use any AI chat or agent (no key needed)", "No AI".
2. API card: provider dropdown (Anthropic, OpenAI, Gemini, OpenAI-compatible), model field with sensible suggestions per provider, key field (masked, "leave empty to keep current"), base URL field shown only for OpenAI-compatible with hints for Ollama (http://localhost:11434/v1) and LM Studio. "Test connection" button with a clear result. Save.
3. Package card, per lecture: "Download package" (zip), "Copy prompt" buttons per part (with "part 1 of 3"), a big paste box plus file upload for the AI's answer, Import. On errors: list them and a "Copy fix message for your AI" button. On success: snippet count and "Open student page".
4. Per lecture "Generate snippets" button when mode is API, with progress from the status endpoint.
5. Student page: read /api/ai-status; if coverage_check is false, nothing changes visibly (no scary warnings for students).
Mock all new endpoints in web/js/mock.js. Screenshot every state at 1280×720 and fix what looks off. Stop.

---

## Extra fixes from round 3 results (do these first, in the same terminal)

T2: Raise SIMILARITY_THRESHOLD to 0.38 in config. Improve ranking with a light keyword boost: BM25 (rank-bm25, or a tiny own implementation) over concept + summary, combined with the embedding score (e.g. 0.8 × cosine + 0.2 × normalised BM25; tune it). Re-run the 12 demo questions in NOTES-lectures.md with search.search and report hit@1/hit@3 before and after. The known misses: "what happens vs. what should happen" (should find positive vs. normative), "mind vs. soul" (dualism), and "split brain" (a "Split brain" clip exists). Keep the threshold-only path working without a key.

T3: In api.py, load the search model and indexes at startup (in a background thread, so the server starts instantly) so the first question isn't slow; /api/health reports "warming" until ready. Add an optional "hidden": true field to meta.json and skip hidden lectures in /api/lectures, /api/examples, search results shown to students and the graph; set it on data/lectures/demo01/meta.json (it's the test fixture, not demo content). Tests still use demo01 directly.

T1 and T4: nothing extra.
