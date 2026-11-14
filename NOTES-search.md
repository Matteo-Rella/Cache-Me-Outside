# T2: Round 6 checkpoint — LLM/settings ready for integration

This section supersedes historical workflow/live-call instructions below. Work is in the existing shared folder with the sibling `cmo-venv`; no Git operations, agents, live AI calls, real lecture edits, live settings changes or databases touched. `rewind/config.py` is unchanged. The following checkpoint was completed and documented **before starting slides/search**.

## Public LLM/settings checkpoint

- `llm.chat(system, messages, max_tokens=800, stream=False, purpose="runtime") -> str | iterator[str]`. Messages are nonempty `{role: "user" | "assistant", content: str}` records. Nonstream uses each native SDK's text API. Stream uses Anthropic `messages.stream`, OpenAI Responses stream, compatible Chat Completions stream, or Gemini `generate_content_stream`. No JSON instructions are added to chat. Callers must close the returned iterator if abandoning it. Provider resources close on completion, cancellation and error. Partial failure, refusal, missing terminal completion, truncation, malformed text and empty/whitespace-only streams raise; no fake success or nonstream downgrade is emitted.
- `llm.ask_json(system, user, max_tokens=16000, purpose="runtime") -> parsed JSON` remains compatible with existing three-argument callers and keeps its existing JSON retry/envelope/provider behavior.
- `purpose` accepts `runtime` or `preprocess`. Both chat and JSON use `preprocess_model` for preprocessing when nonempty, otherwise `model`. Runtime always uses `model`; one settings snapshot is used for each request/retry sequence.
- `settings.load() -> dict`, `settings.save(partial_dict) -> None`, `settings.public() -> dict` retain precedence, atomic writes and masking. Added `chat_policy` (`strict` default or `labelled`) and `preprocess_model` (blank means runtime model). Environment defaults are `LLM_CHAT_POLICY` and `LLM_MODEL_PREPROCESS`; saved values override them, including an explicit blank preprocess model. Both fields appear in `public()`, and existing settings files need no migration. No config-file change was needed.
- `llm.estimate(requests, purpose="runtime") -> dict` accepts the actual API shape `[{system: str, user: str, max_tokens: positive int, purpose?: "runtime" | "preprocess"}]`. Request-level purpose overrides the call default, supporting mixed requests. Result contains `calls`, `input_tokens`, `output_tokens`, `cost_usd`, **`est_cost_usd`** (UI alias), `currency: "USD"`, `approximate: true`, `pricing_known`, `pricing_checked_at`, `model` (null for mixed models), `purpose`, and per-request `requests` details. No SDK/network access occurs. Input is approximated by UTF-8 bytes/3 + 64 framing tokens per request; output uses the supplied cap. This excludes unlisted verification requests, retries, tools, provider fees and cache discounts. Unknown model/endpoint pricing returns null in both cost fields, never zero. Empty request lists correctly cost zero.
- Only the exact OpenRouter endpoint/model combinations `anthropic/claude-sonnet-5` ($2/$10 per million input/output tokens) and `anthropic/claude-haiku-5.5` ($0.10/$0.50) are priced. Reverified **2026-11-14** against [OpenRouter Sonnet 5](https://openrouter.ai/anthropic/claude-sonnet-5) and [OpenRouter Haiku 5.5](https://openrouter.ai/anthropic/claude-haiku-5.5). Historical prices were not assumed; native providers and arbitrary compatible endpoints remain unknown unless separately verified.
- Existing `available()`, `max_input_chars()` and `test_connection() -> {ok, message, latency_ms}` remain available. No live connection test was performed in Round 6.

## Checkpoint tests and handoff

`..\\cmo-venv\\Scripts\\python.exe -m pytest -q tests/test_llm.py tests/test_settings.py`: **145 passed**. Tests isolate runtime directories/environment and mock all providers. New coverage includes all four providers' native text/stream paths, partial failure, cancellation cleanup, truncation, missing terminal events, empty/whitespace streams, purpose routing/fallback, malformed histories, new settings validation/precedence, mixed-purpose estimates and unknown prices. Existing JSON/provider tests still pass. SDKs remain lazy imports.

- **T3:** integrate the exact `chat`/`estimate` purpose signatures and save/public fields above. UI consumes `est_cost_usd`; API/tests may use `cost_usd`; both are present. Null cost means unknown pricing. No endpoint/store integration is claimed by this checkpoint.
- **T4:** consume the native text iterator with `stream=True`, close it when cancelling, and preserve provider errors as failures rather than a completed assistant turn. Preprocessing can now pass `purpose="preprocess"` directly. These tests do not establish grounded answer quality.
- **T1:** `chat_policy` is now persisted and publicly returned; `preprocess_model` is also exposed. Price estimates have an explicit approximate/unknown state.
- Foundation blockers: none observed in scoped mocked checks. Combined API/chat/browser tests belong to the coordinator/other workers. Next: slides and slide search using generated/temporary fixtures only.

## Round 6 completed — slides/search checkpoint

The LLM/settings checkpoint above was published first. Both stages are now complete within T2's scope. No real lecture JSON/assets, live settings, databases or `.env` were changed. No real assets were downloaded, no LLM calls were made, and no other workers' files/tests were edited. `rewind/config.py` remains unchanged. Required dependencies were appended only: `PyMuPDF` and `python-pptx`, and installed in the existing sibling venv.

Public interfaces:

- `slides.ingest_slides(lecture_id, path) -> int`: number of pages/slides imported. PDF extracts text and renders one PNG per page at approximately 1280 pixels wide (height capped at 4096 for extreme page shapes). PPTX extracts text from normal text boxes, groups and tables. Optional LibreOffice conversion adds PDF-derived previews; absent/failed/unsafe conversion gives text-only records and an explicit export-to-PDF warning. Missing PDF/PPTX packages raise useful unavailable errors, not fake imported results.
- `slides.load_slides(lecture_id) -> list[dict]`: records `{n: int, title: str, text: str, image: str | None}`; missing manifest returns `[]`. Additional existing metadata is preserved. Invalid records/unsafe paths raise. Missing image files produce an honest `image: None` text-only state. Image paths are `slides/slide-NNN-<source-hash>.png`, compatible with the existing API's plain-file URL helper.
- `slides.align(lecture_id) -> int`: number of snippets receiving matches. Uses the existing search embedding model; assigns each snippet the best **up to two** slide numbers at cosine >= **0.45**. Changes only `snippet["slides"]`, preserving questions, timestamps, review/provenance and other fields. Stale slide links are cleared when no text slides remain. No-snippets/no-slides needs no model. Concurrent source/slide edits cause a retry error instead of overwriting another worker's changes. Alignment does not imply professor approval or enforce publication state.
- `search.index_lecture(lecture_id) -> None`, `index_all() -> None`: index clip text as before and slide title/text in `slides/embeddings.npy` plus `slides/embeddings.ids.json`. Decks with no `snippets.json` are supported without creating a fake snippet file. Empty/unextractable slide text is excluded from retrieval.
- `search.search(question, top_k=3) -> list[{snippet, score}]` retains its clip contract and existing cosine/BM25 ranking. `search.search_slides(question, top_k=2) -> list[{lecture_id, n, score}]` exposes raw cosine ranks, including below-threshold hits.
- `search.answer(question) -> {status, best, alternatives, score, slides}`: `status` is `found`, `not_covered` or **`slide_only`**; `best` remains a plain snippet dict for `found`, otherwise `None`; alternatives remain snippet dicts. `slides` is always a list (empty when absent), containing up to two `{lecture_id, n, score}` hits at the existing configured similarity threshold (currently 0.38). A qualifying slide yields `slide_only` when no clip passes, including when the mocked coverage check rejects the clip. In that status, score is the best slide cosine. Existing callers can ignore the additive slides field. No-key/package/none paths still work without a coverage call.

Safety/invalidation:

- Lecture paths reject traversal and symlink/junction aliases; slide manifests/image paths stay within that lecture's slides directory. Source files are read into bounded private snapshots and never moved or rewritten. Source cap is 50 MiB; decks cap at 500 slides; PPTX archive entry/expanded-size/path limits are checked. Active/external PPTX assets disable optional conversion rather than being fetched or executed. LibreOffice runs without a shell under a private profile and bounded timeout; no existing process is stopped.
- Images use content-addressed names so a replacement source doesn't collide with cached previews. Metadata and alignment writes are atomic; unrelated files in slides/ are retained. Old unreferenced generated previews are deliberately retained rather than deleting user/local files.
- Slide caches bind to source signatures/hash, ordered slide numbers, configured embedding model and vector dimensions. Missing/corrupt indexes rebuild automatically. Replaced/deleted decks and model changes invalidate search results. Existing clip/index caches remain in use. Blank/image-only decks retain previews but need OCR before text retrieval; no OCR success is fabricated.

Final tests and visual check:

`..\\cmo-venv\\Scripts\\python.exe -m pytest -q tests/test_llm.py tests/test_settings.py tests/test_slides.py tests/test_search.py`: **197 passed, 1 dependency warning**, in 1.91 seconds. The warning is PyMuPDF's `typing.ByteString` deprecation under the existing Python 3.15 interpreter, not an application failure. The prior LLM/settings checkpoint was 145 passing tests before slides work; the final run includes an additional blank-environment chat-policy regression and slide contract adjustments.

Fixtures are generated three-page PDF/PPTX decks and temporary lectures; embedding functions/providers are mocked. Coverage includes source preservation, PDF parsing failure without destroying an existing deck, missing dependency behavior, PPTX groups/tables, text-only/optional preview conversion, traversal/unsafe archive metadata, 1–2-slide alignment, preservation of unrelated snippet fields and concurrent edits, slide-only retrieval, clip+slide results, LLM clip rejection with a slide answer, no-slides/blank-slide behavior, source deletion/model change, cache reuse and damaged-index rebuilding. The Windows invalid-PDF file-handle failure was fixed by opening bounded bytes rather than holding the source file open on a failed parse.

All three generated PDF previews were visually inspected at **1280x720**: titles and body text are readable, with no clipping or overlap. These are temporary validation artifacts, not real lecture assets or final deliverables. Poppler/LibreOffice are not installed here; PDF previews were rendered using the explicitly requested PyMuPDF pipeline. Actual LibreOffice conversion is **not** claimed verified; that success path is mocked, while the absent-converter text-only path is tested.

Remaining limitations and named handoffs:

- **T3:** consume the exact return shapes above; enrich raw slide references using `load_slides`. Preserve the local-only upload/media guards and reindex after import/generation/alignment. For text-only PPTX responses (`image is None`), expose the instruction to export a PDF; the module emits a warning but cannot itself display an API/UI banner. API/store/ingest integration tests were not run by T2.
- **T1:** tolerate `slides: []`, `image/image_url: None`, and `slide_only` with `best: None`; display slide text or an honest preview-unavailable state and tell the lecturer to export PDF for previews. Do not imply alignment means professor approval. No web/browser integration is claimed by T2.
- **T4:** `load_slides` is now available directly for selected-lecture chat context. Keep material text as data. Catch/propagate malformed material and stream failures honestly. No grounded chat quality claim follows from these retrieval/provider mocks.
- **Coordinator:** run the combined suite and real local integration after workers stop. Historical tests/metrics/live prices below are not current authorization. Add generated source/settings/index paths to ignore rules as appropriate; T2 did not change `.gitignore`. Existing runtime dependencies for native Gemini remain an environment concern if `google-genai` is absent; mocks do not establish live SDK readiness. No real retrieval/model-quality benchmark or professor-approval enforcement was performed in this round.

---

# Historical Round 4 notes (not current authorization)

Completed the T2 extras in order: OpenRouter support and live ping/evaluation, search ranking fixes, then settings and the provider contract. All work stayed in `Cache-Me-Outside`. No git commands, worktrees, commits, server starts or process stops in this round. Only T2 files were edited, including the search change explicitly assigned in the extra fixes.

## Settings contract and integration

- `rewind.settings.load()` returns effective private settings. Precedence is `data/settings.json` > environment / `.env` > defaults. On first load without a file, mode is `api` when key and model are configured, otherwise `none`. `LLM_MODE` can explicitly select a mode, including API mode for a keyless local server.
- `save(dict)` validates and atomically persists partial updates. Omitted `api_key` keeps the current key; an explicit empty string clears it. Masked keys cannot accidentally overwrite a real key. Invalid updates leave the existing file intact.
- `public()` returns masked keys (`sk-…a1b2`; entirely masked for short custom keys) and a derived `preset`: `openrouter`, `ollama`, `lmstudio`, or null. `preset` is read-only and is not a saved input field. Full keys and raw SDK error messages are never included in public settings or connection results.
- Fields: `mode`, `provider`, `model`, `api_key`, `base_url`, plus optional `max_input_chars` (positive integer; null means automatic). Base URLs must be HTTP(S) URLs without embedded credentials, query parameters or fragments.
- All modes preserve stored credentials so the lecturer can switch back to API mode. `llm.available()` is true only in API mode with a model plus a key, or a compatible base URL in place of the key.
- **For the .gitignore owner:** add `data/settings.json` (contains the private key). Also consider `**/embeddings.ids.json` for the generated index manifest. Neither `.gitignore` nor the real `.env` was edited by T2. Runtime settings were exercised in temporary directories only.
- **For T3:** use `settings.public()` for every browser response, not `load()`. Local-only endpoint/page guards remain T3's responsibility. `search.search()` still includes fixture/hidden lectures as a raw retrieval API; student-visible filtering remains T3's responsibility.
- **For T4:** use `llm.max_input_chars()` when splitting API transcripts. Defaults are Anthropic/OpenAI 100,000 characters, Gemini 200,000, and compatible servers 12,000 (conservative for small local models). They are input budgets, not guarantees about a particular model's token window; a saved `max_input_chars` or `LLM_MAX_INPUT_CHARS` overrides them.

## LLM providers and no-key behavior

- `ask_json(system, user, max_tokens=16000)` retains its signature. It reads one settings snapshot for a request and its JSON retry, so an admin save cannot mix providers/keys mid-request.
- Official SDKs remain lazy imports. No requirements changes were needed this round: `anthropic`, `openai`, and `google-genai` were already listed. The already-declared OpenAI SDK was installed in the shared venv for the explicitly requested live check.
- `openai_compatible` uses the OpenAI SDK's **Chat Completions** API with `base_url`, not Responses. Empty local keys use the SDK placeholder `ollama`. Compatible clients use a 30-second timeout and no automatic SDK retries.
- JSON mode requests `response_format={"type":"json_object"}`. A format-specific 400/404/422 rejection gets one retry without that option. Authentication/network/rate errors are not retried as format failures. Generic JSON parsing still retries invalid JSON once with conversation context.
- OpenRouter receives `HTTP-Referer: http://localhost:8000` and `X-Title: Rewind`. `.env.example` includes OpenRouter, Ollama and LM Studio examples. No keys or provider model IDs are hardcoded in production code.
- Native OpenAI retains Responses with JSON mode; Gemini uses `application/json`; Anthropic retains its streamed JSON-prompt path. OpenAI-format calls use an internal `result` envelope to preserve callers expecting arrays; it is removed before returning to callers. Compatible servers also tolerate direct JSON replies.
- `test_connection()` sends a tiny JSON probe and returns `{ok, message, latency_ms}`. Failures explain bad authentication, missing model/endpoint, insufficient credit, rate limiting, unreachable/time-out servers, missing SDKs or malformed JSON without echoing secrets.
- `none`, `package`, missing-key and missing-model paths skip the LLM coverage check and keep threshold-only retrieval; homework detection uses keywords only. `best` stays a **plain snippet dict** (or null), and alternatives stay snippet dicts. API failures with configured credentials still propagate instead of falsely declaring an uncovered topic.
- One server-side warning is emitted on an unconfigured module startup, with no warnings per search/classification call.

## Search ranking and the 12 round-3 demo questions

`SIMILARITY_THRESHOLD` is now **0.38**. Added a dependency-free BM25 index over concept + summary, cached separately from the existing model/embedding caches. Scores combine **88% cosine + 12% normalized BM25**; explicit comparison questions use **80% / 20%** and a generic comparison-intent marker for concepts containing words such as `vs`. This is not a list of lecture-specific synonym substitutions. Possessives and common question scaffolding are filtered, while `should` is retained because it carries normative meaning.

I tested the suggested 20% keyword weight on ordinary questions; it changed equilibrium and face-recognition questions to worse results. The lighter weight preserves those hits. These weights are tuned on a small demo set and should be checked on more lectures before claiming general improvements.

Re-ran exactly the 12 table questions in `NOTES-lectures.md` through `search.search`. Corpus was the same three folders (`demo01`, `econ01`, `psych02`) for before/after; the concurrent temporary `econ01-api` folder was excluded from the comparison. Baseline was obtained by setting keyword weight to zero in the evaluation process only. Strict expected IDs were used: positive/normative `econ01-010/011`, dualism `psych02-001/002/010` (not the arguably acceptable `005`). There are 10 answerable questions and 2 gaps.

| Metric | Cosine baseline | Final ranking |
|---|---:|---:|
| hit@1, answerable questions | 8/10 (80%) | **10/10 (100%)** |
| hit@3, answerable questions | 8/10 (80%) | **10/10 (100%)** |
| gaps caught by threshold alone | 1/2 | 1/2 |

| Question topic | Final first result | Score |
|---|---|---:|
| Water vs diamonds | econ01-007 | 0.714 |
| Hidden cost of choices | econ01-002 | 0.575 |
| Price where buyers/sellers agree | econ01-009 | 0.421 |
| What happens vs what should happen | **econ01-010** | **0.532** |
| Price elasticity (not covered) | econ01-008 | 0.520 |
| Equilibrium homework | econ01-009 | 0.576 |
| Mind, brain and soul | **psych02-010** | **0.582** |
| Neuron messages | psych02-017 | 0.734 |
| Alcohol and inhibition | psych02-019 | 0.535 |
| Cannot recognize faces | psych02-030 | 0.574 |
| Long-term memory (not covered) | econ01-015 | **0.305**, rejected |
| Agonists vs antagonists homework | psych02-018 | 0.614 |

The extra split-brain check (`What happens in a split brain patient?`) was already correct before: `psych02-033` first at 0.659. It stays first after, at **0.680**. It is separate from the 12-question table.

Elasticity remains above the threshold despite being absent from these excerpts; it needs the LLM check. Raising the threshold enough to reject it would lose valid questions. The no-key path intentionally cannot guarantee that related-but-unexplained questions are rejected.

## Live OpenRouter checks (explicitly requested)

The prompt file initially lacked AI COSTS; it was subsequently updated and re-read, including the current Contract and RULES. The user clarified that the configured model is cheap and the project has €10 credit. This round made one tiny real ping and two **capped 10-item** evaluation runs (before and after ranking/settings). No live calls were made inside unit tests, and T2 ran no transcript generation. The key was never printed or logged. No exact spend is claimed.

- `llm.ask_json('Reply with JSON {"ok": true}', 'ping', 50)`: **succeeded** using the configured OpenRouter model. This prerequisite was reported before T4's generation work.
- `python tools/eval_search.py`: default `tests/search_eval.json`, 10 questions, all three lecture folders included. Six answerable questions, four gaps. Both runs used a real coverage check; this is separate from the 12 round-3 questions above.

| Metric | Before, threshold 0.35 | Final, threshold 0.38 |
|---|---:|---:|
| Threshold-only hit@1 | 4/6 (66.7%) | 4/6 (66.7%) |
| Retrieval hit@3 | 5/6 (83.3%) | **6/6 (100%)** |
| Threshold-only gaps caught | 2/4 | 2/4 |
| With LLM: hit@1 | 5/6 (83.3%) | **6/6 (100%)** |
| With LLM: gaps caught | 4/4 | **4/4** |
| With LLM: gap precision | 4/5 (80%) | **4/4 (100%)** |
| With LLM: gap recall | 4/4 (100%) | **4/4 (100%)** |

The before run's retrieval recall is lower than the original fixture-only experiment because this run also searches the real economics/psychology lectures. The final LLM run found all six expected fixture snippets and correctly rejected all four gaps. These are demo metrics on ten items, not a large validation set.

## Unit tests

**119 passed**, only `tests/test_settings.py`, `tests/test_llm.py`, and search-owned `tests/test_search.py`. Coverage includes precedence, secret masking, presets, atomic updates, validation, three native SDKs and compatible Chat Completions, JSON retry/fallback, configured/non-API modes, no-key behavior, connection error messages, input budgets and ranking behavior. All provider SDKs/replies are mocked in these tests.

The test launcher isolates `config.DATA_DIR` in a temporary directory and provides dummy credentials only to its own process so existing search tests exercise their mocked coverage decisions. No real settings file or `.env` is modified. Reproduce from `Cache-Me-Outside`:

```powershell
@'
import os, tempfile
from pathlib import Path
import pytest
from rewind import config
with tempfile.TemporaryDirectory(prefix='rewind-t2-tests-') as directory:
    config.DATA_DIR = Path(directory)
    os.environ.update(LLM_PROVIDER='anthropic', LLM_API_KEY='test-key-not-real', LLM_MODEL='test-model', LLM_MODE='api', LLM_BASE_URL='')
    raise SystemExit(pytest.main(['-q', 'tests/test_settings.py', 'tests/test_llm.py', 'tests/test_search.py']))
'@ | & ../cmo-venv/Scripts/python.exe -
```

For the owners of existing quiz/coverage tests: mock `llm.available()` explicitly when testing an LLM decision, rather than relying on a real `.env` or settings file. Those unrelated tests were not run in this round.

The available interpreter is Python 3.15.2 in `../cmo-venv`; Python 3.11 was unavailable. Code uses Python 3.11-compatible syntax. Documentation checked: [official OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create), [OpenRouter](https://openrouter.ai/docs/quickstart), [Google Gen AI SDK](https://googleapis.github.io/python-genai/), [Anthropic SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python).
