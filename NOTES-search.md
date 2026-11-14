# Search implementation

- Added multilingual cosine retrieval, cached model/index loading, automatic indexing, and a provider-independent excerpt-coverage check. `SIMILARITY_THRESHOLD` remains 0.35.
- Each generated `embeddings.npy` has an `embeddings.ids.json` manifest with ordered snippet ids, model name, and source hash. Changed source files, missing/corrupt indexes, and model changes trigger rebuilding. These are generated runtime data.
- `answer()` keeps `best` as a **plain snippet dictionary** (or `None`), as required by `store.log_ask` and `app.py`. Alternatives are also snippet dictionaries. For a found answer, `score` is the selected snippet's similarity; for a rejection it is the highest retrieval similarity, or 0.0 for an empty corpus. Without LLM configuration, search uses the threshold alone. Configured API errors propagate instead of being reported as lecture gaps.
- Added exactly 10 demo01 evaluation questions: six answerable (including two Italian), two unrelated, and two on-topic topics absent from the excerpt. The LLM must distinguish explained content from promises to explain it later.

## Validation

- Latest validation, run from `Cache-Me-Outside`: **61 passed** across `tests/test_llm.py`, `tests/test_search.py`, and `tests/test_quiz.py`. No other tests were run for the provider/fallback task.
- Existing search/quiz tests assume the LLM check is enabled while mocking its reply. They were run with `LLM_PROVIDER=anthropic`, `LLM_API_KEY=test-key-not-real`, and `LLM_MODEL=test-model` supplied only to the pytest subprocess. No `.env` was created or changed, and no live API calls were made. The new LLM tests clear these settings per test and explicitly verify the no-key paths. To run the same selection in PowerShell without changing the parent environment:

  ```powershell
  @'
  import os, subprocess, sys
  settings = os.environ.copy()
  settings.update(LLM_PROVIDER='anthropic', LLM_API_KEY='test-key-not-real', LLM_MODEL='test-model')
  raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', '-q', 'tests/test_llm.py', 'tests/test_search.py', 'tests/test_quiz.py'], env=settings))
  '@ | & ../cmo-venv/Scripts/python.exe -
  ```

- Follow-up for the owners of existing search/quiz tests: explicitly mock `llm.available()` to `True` in tests exercising LLM decisions, so those tests can also run without process-level dummy configuration. Those test files were not edited in this task.
- A real-model smoke check found `demo01-002` for an obvious coefficient-matrix question with only the coverage response mocked. A cake question was rejected without invoking the LLM.
- `python tools/eval_search.py --skip-llm`, using the real embedding model and threshold 0.35:
  - hit@1: **66.7% (4/6)**.
  - hit@3 (retrieval candidates): **100.0% (6/6)**. The fixture only has three snippets, so this is a limited recall check.
  - not_covered caught: **2/4**; precision **100.0% (2/2)**; recall **50.0% (2/4)**.
- Threshold comparison: 0.35 accepts 6/6 answerable questions and catches 2/4 gaps; 0.45 accepts 5/6 and catches 2/4; 0.60 accepts 1/6 and catches 4/4. Kept 0.35: threshold increases lose answerable questions before separating the related gaps (scores 0.516 and 0.571). Full LLM evaluation is still needed; semantic similarity alone cannot distinguish these cases.
- `python tools/eval_search.py` runs the same metrics with and without the LLM check. `--threshold FLOAT` supports further tuning. Live Anthropic evaluation was not run because neither `ANTHROPIC_API_KEY` nor `ANTHROPIC_MODEL` is configured. No live LLM metrics are claimed.
- The available interpreter is Python 3.15.2 in `../cmo-venv`; Python 3.11 was unavailable. The implementation uses Python 3.11-compatible syntax.

## Provider configuration and no-key behavior

- `LLM_PROVIDER=anthropic|openai|gemini` (default Anthropic), `LLM_API_KEY`, and `LLM_MODEL` are read from the environment, with optional `.env` loading. No provider model ids or keys are hardcoded. For Anthropic only, `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` are fallbacks; generic values take precedence. Legacy Anthropic credentials are never forwarded to another provider.
- `config.get_model()` returns an empty string when unset. `llm.available()` checks for a supported provider plus a key and model, without importing any SDK or making a request. An invalid provider raises a clear configuration error.
- `ask_json(system, user, max_tokens=16000)` retains its signature and returns the requested parsed JSON value. SDKs are imported only when used. `anthropic` was already in `requirements.txt`; appended `openai` and `google-genai` without changing existing lines.
- OpenAI uses the Responses API with JSON mode and an internal `{"result": ...}` envelope, removed before returning to callers. This preserves array responses needed by snippet generation. Gemini uses `response_mime_type="application/json"`. Anthropic retains its streamed JSON-prompt path; the common API has no schema parameter for provider-specific structured output. Invalid JSON is retried once with conversation context; truncations, refusals, and API errors are not treated as valid answers.
- With no key (or no model), `search.answer()` skips coverage classification and returns the top threshold-qualified snippet in the existing shape. `quiz.is_homework()` uses the existing keyword/numbered-problem rules only. Direct `ask_json()` calls still raise a clear configuration error rather than inventing generated content.
- One warning is emitted when `rewind.llm` is first imported without configuration. `available()`, search, and homework calls do not log repeated warnings.
- `.env.example` documents each provider and legacy settings. SDK adapter tests use mocked official SDK modules; no live provider validation is claimed.
- Documentation checked: [OpenAI JSON mode](https://developers.openai.com/api/docs/guides/structured-outputs), [Google Gen AI SDK](https://googleapis.github.io/python-genai/), and [Anthropic Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python).

## Current working rules

The user's updated rules override the earlier workflow in `AGENTS.md`: work only in the shared `Cache-Me-Outside` folder, run no git commands, create no worktrees or commits, and never stop another terminal's processes. This provider/fallback task edits only `rewind/llm.py`, `rewind/config.py`, `.env.example`, `tests/test_llm.py`, small fallback changes in `rewind/search.py` and `rewind/quiz.py`, and this note, plus the explicitly requested append-only SDK additions to `requirements.txt`. No git commands were run and no processes were stopped in this task. Changes needed in other files are recorded above.
