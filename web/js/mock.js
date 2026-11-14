// Mock backend: answers every endpoint of the API contract (docs/round2-prompts.md)
// in the browser, using the demo01 lecture copied into mock-data.js.
// Used when the URL has ?mock=1 or /api/health is unreachable.
import { MOCK_META, MOCK_SNIPPETS } from "./mock-data.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const LS_KEY = "rewind.mock.state";

function youtubeId(url) {
  const m = String(url).match(/(?:v=|youtu\.be\/|embed\/|shorts\/)([\w-]{11})/);
  return m ? m[1] : null;
}

const LECTURE = {
  ...MOCK_META,
  video_type: youtubeId(MOCK_META.video_url) ? "youtube" : "file",
  youtube_id: youtubeId(MOCK_META.video_url),
  snippet_count: MOCK_SNIPPETS.length,
};

const publicSnippet = (s) => ({
  id: s.id, lecture_id: s.lecture_id, concept: s.concept, start: s.start, end: s.end, summary: s.summary,
  questions: s.questions.map((q) => ({ q: q.q, options: q.options })),
});

// Realistic seeded activity so the lecturer page has something to show.
function seededState() {
  return {
    stats: {
      "demo01-001": { times_asked: 14, attempts: 22, wrong: 5 },
      "demo01-002": { times_asked: 23, attempts: 37, wrong: 18 },
      "demo01-003": { times_asked: 9, attempts: 15, wrong: 3 },
    },
    asks: { found: 46, not_covered: 7 },
    gaps: [
      { gap_id: 1, question: "how do you compute a determinant?", count: 3, last_asked: daysAgo(0.2) },
      { gap_id: 2, question: "what is an eigenvalue?", count: 2, last_asked: daysAgo(1.1) },
      { gap_id: 3, question: "when does a system have no solution?", count: 2, last_asked: daysAgo(2.4) },
      { gap_id: 4, question: "what's the difference between rank and dimension?", count: 1, last_asked: daysAgo(3) },
      { gap_id: 5, question: "is gaussian elimination on the exam?", count: 1, last_asked: daysAgo(4.5) },
    ],
    wrong: {}, // student_id -> {snippet_id: true}
  };
}
function daysAgo(d) { return new Date(Date.now() - d * 864e5).toISOString(); }

let mockJob = null;
let state = load();
function load() {
  try { const s = JSON.parse(localStorage.getItem(LS_KEY)); if (s && s.stats) return s; } catch {}
  return seededState();
}
function save() { try { localStorage.setItem(LS_KEY, JSON.stringify(state)); } catch {} }

const STOP = new Set("a an the is are was what whats what's how why does do did of to in on and or for with i me my you it this that be can about explain mean means".split(" "));
const words = (t) => String(t).toLowerCase().normalize("NFKD").replace(/[^\w\s]/g, " ").split(/\s+/).filter((w) => w.length > 1 && !STOP.has(w));
const HOMEWORK = /\b(solve|calculate|compute|find the|risolvi|calcola|esercizio|homework|exercise|problem \d)\b/i;

function search(question) {
  const qw = words(question);
  const scored = MOCK_SNIPPETS.map((s) => {
    const hay = new Set(words(`${s.concept} ${s.concept} ${s.summary} ${s.text}`));
    const stems = [...hay].map((w) => w.slice(0, 5));
    let score = 0;
    for (const w of qw) if (hay.has(w)) score += 1; else if (stems.includes(w.slice(0, 5))) score += 0.6;
    return { s, score: qw.length ? score / qw.length : 0 };
  }).sort((a, b) => b.score - a.score);
  return scored;
}

function totals() {
  const st = Object.values(state.stats);
  const asks = state.asks.found + state.asks.not_covered;
  const rates = st.filter((x) => x.attempts).map((x) => x.wrong / x.attempts);
  return {
    asks,
    found_pct: asks ? Math.round((1000 * state.asks.found) / asks) / 10 : 0,
    not_covered_pct: asks ? Math.round((1000 * state.asks.not_covered) / asks) / 10 : 0,
    avg_fail_rate: rates.length ? Math.round((1000 * rates.reduce((a, b) => a + b, 0)) / rates.length) / 1000 : 0,
  };
}

const routes = [
  ["GET", /^\/api\/health$/, () => ({ ok: true, mode: "fake" })],
  ["GET", /^\/api\/lectures$/, () => [LECTURE]],
  ["GET", /^\/api\/examples$/, () => [
    "Why do we write a system as Ax = b?",
    "What's the coefficient matrix of a system?",
    "What are the row and column pictures?",
  ]],
  ["POST", /^\/api\/ask$/, async (_, body) => {
    await sleep(1400 + Math.random() * 900);
    const homework = HOMEWORK.test(body.question);
    const ranked = search(body.question);
    const found = ranked[0].score >= 0.34;
    if (found) state.asks.found++; else state.asks.not_covered++;
    if (found) state.stats[ranked[0].s.id].times_asked++;
    else {
      const q = body.question.trim().toLowerCase();
      const g = state.gaps.find((x) => x.question === q);
      if (g) { g.count++; g.last_asked = new Date().toISOString(); }
      else state.gaps.unshift({ gap_id: Date.now(), question: q, count: 1, last_asked: new Date().toISOString() });
    }
    save();
    if (!found) return { status: "not_covered", homework, snippet: null, lecture: null, alternatives: [] };
    return {
      status: "found", homework,
      snippet: publicSnippet(ranked[0].s), lecture: LECTURE,
      alternatives: ranked.slice(1, 3).map((r) => ({ snippet: publicSnippet(r.s), lecture: LECTURE })),
    };
  }],
  ["POST", /^\/api\/answer$/, async (_, body) => {
    await sleep(250);
    const s = MOCK_SNIPPETS.find((x) => x.id === body.snippet_id);
    if (!s) throw new Error("unknown snippet");
    const q = s.questions[body.question_index];
    const correct = Number(body.chosen) === q.answer;
    const st = state.stats[s.id]; st.attempts++; if (!correct) st.wrong++;
    const w = (state.wrong[body.student_id] ||= {});
    if (correct) delete w[s.id]; else w[s.id] = Date.now();
    save();
    return { correct, correct_index: q.answer, explanation: q.explanation };
  }],
  ["GET", /^\/api\/students\/([^/]+)\/rewatch$/, (m) => {
    const w = state.wrong[decodeURIComponent(m[1])] || {};
    return Object.entries(w).sort((a, b) => b[1] - a[1])
      .map(([id]) => ({ snippet: publicSnippet(MOCK_SNIPPETS.find((s) => s.id === id)), lecture: LECTURE }));
  }],
  ["GET", /^\/api\/lecturer\/stats$/, () => ({
    totals: totals(),
    snippets: MOCK_SNIPPETS.map((s) => {
      const x = state.stats[s.id];
      return {
        snippet_id: s.id, concept: s.concept, lecture_id: s.lecture_id, lecture_title: MOCK_META.title,
        start: s.start, end: s.end, times_asked: x.times_asked, attempts: x.attempts,
        fail_rate: x.attempts ? Math.round((1000 * x.wrong) / x.attempts) / 1000 : 0,
      };
    }),
  })],
  ["GET", /^\/api\/lecturer\/gaps$/, () => state.gaps],
  ["POST", /^\/api\/lecturer\/gaps\/([^/]+)\/resolve$/, (m) => {
    state.gaps = state.gaps.filter((g) => String(g.gap_id) !== decodeURIComponent(m[1])); save();
    return { ok: true };
  }],
  ["POST", /^\/api\/lecturer\/seed$/, async () => { await sleep(500); state = seededState(); save(); return { ok: true }; }],
  ["POST", /^\/api\/questions$/, (_, body) => {
    state.gaps.unshift({ gap_id: Date.now(), question: String(body.question || "").trim(), count: 1, last_asked: new Date().toISOString() }); save();
    return { ok: true };
  }],
  ["POST", /^\/api\/lecturer\/lectures$/, (_, body) => { mockJob = { job_id: "mock", lecture_id: "mock01", title: body.title, state: "running", step: "upload", percent: 0, message: "", t0: Date.now() }; return mockJob; }],
  ["GET", /^\/api\/lecturer\/uploads\/([^/]+)$/, () => {
    const s = (Date.now() - mockJob.t0) / 1000;
    if (s < 5) return { ...mockJob, step: "transcribe", percent: Math.round(20 * s) };
    if (s < 7) return { ...mockJob, step: "moments", percent: 100 };
    return { ...mockJob, state: "done", step: "done", percent: 100, snippet_count: 0, message: "Browser demo: nothing was really uploaded." };
  }],
  ["GET", /^\/api\/graph$/, () => {
    const course = `course:${MOCK_META.course}`;
    const lec = `lecture:${MOCK_META.lecture_id}`;
    const nodes = [
      { id: course, label: MOCK_META.course, type: "course" },
      { id: lec, label: `Lecture ${MOCK_META.lecture_number}`, type: "lecture" },
    ];
    const edges = [{ from: course, to: lec }];
    for (const s of MOCK_SNIPPETS) {
      const x = state.stats[s.id];
      nodes.push({ id: `snippet:${s.id}`, label: s.concept, type: "snippet", snippet_id: s.id,
        times_asked: x.times_asked, fail_rate: x.attempts ? x.wrong / x.attempts : 0 });
      edges.push({ from: lec, to: `snippet:${s.id}` });
    }
    // Concept nodes: shared ideas that link snippets.
    const concepts = { "Linear equations": ["demo01-001", "demo01-003"], "Matrix": ["demo01-002", "demo01-003"], "Vectors": ["demo01-002", "demo01-003"] };
    for (const [c, ids] of Object.entries(concepts)) {
      nodes.push({ id: `concept:${c.toLowerCase()}`, label: c, type: "concept" });
      for (const id of ids) edges.push({ from: `snippet:${id}`, to: `concept:${c.toLowerCase()}` });
    }
    return { nodes, edges };
  }],
];

export async function mockFetch(method, path, body) {
  for (const [m, re, fn] of routes) {
    const match = m === method && path.match(re);
    if (match) {
      await sleep(120);
      return structuredClone(await fn(match, body || {}));
    }
  }
  throw new Error(`mock: no route for ${method} ${path}`);
}

// ---------------------------------------------------------------- round 4: AI settings, context package, generate

const SETTINGS_KEY = "rewind.mock.settings";
const DEFAULT_SETTINGS = { mode: "api", provider: "openai_compatible", preset: "openrouter", model: "anthropic/claude-haiku-5.5", api_key: "sk-or-v1-demo-0000a1b2", base_url: "https://openrouter.ai/api/v1" };
const presetOf = (s) => (s.provider !== "openai_compatible" ? null
  : /openrouter\.ai/.test(s.base_url) ? "openrouter" : /:11434/.test(s.base_url) ? "ollama" : /:1234/.test(s.base_url) ? "lmstudio" : null);
let settings = (() => { try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY)) }; } catch { return { ...DEFAULT_SETTINGS }; } })();
const persistSettings = () => { try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch {} };
const mask = (k) => (k ? `${k.slice(0, 3)}…${k.slice(-4)}` : "");
const publicSettings = () => ({ ...settings, preset: presetOf(settings), api_key: mask(settings.api_key) });
const canGenerate = () => settings.mode === "api" && !!settings.model &&
  (!!settings.api_key || (settings.provider === "openai_compatible" && !!settings.base_url));
const genStatus = {}; // lecture_id -> {started}

function mockPromptParts() {
  const lines = MOCK_SNIPPETS.map((s) => `[${s.start.toFixed(1)}] ${s.text}`);
  const head = "You cut a recorded university lecture into short, self-contained topic snippets… (full instructions, JSON schema and one example)\n\nReturn JSON only, inside one ```json block.";
  return lines.map((slice, i) =>
    `# Rewind · ${MOCK_META.title} · part ${i + 1} of ${lines.length}\n\n${head}\n\n` +
    `${i ? `Continue from part ${i}. Output only snippets for this slice.\n\n` : ""}Transcript:\n${slice}`);
}

/** Pull JSON out of a chat reply: code fences, prose around it, several parts pasted together. */
function extractSnippets(raw) {
  const text = String(raw);
  const blocks = [...text.matchAll(/```(?:json)?\s*([\s\S]*?)```/g)].map((m) => m[1]);
  const candidates = blocks.length ? blocks : [text.slice(Math.max(0, text.search(/[[{]/)))];
  const out = [];
  for (const c of candidates) {
    const v = JSON.parse(c.trim());
    out.push(...(Array.isArray(v) ? v : v.snippets || []));
  }
  return out;
}

function mockImport(raw) {
  const errors = [];
  let items = [];
  try { items = extractSnippets(raw); }
  catch (e) { errors.push(`The answer is not valid JSON (${e.message}). Put the whole answer in one json code block.`); }
  if (!errors.length && !items.length) errors.push("No snippets found. The answer must be a JSON list of snippets.");
  items.forEach((s, i) => {
    const n = `Snippet ${i + 1}${s && s.concept ? ` (“${s.concept}”)` : ""}`;
    if (!s || typeof s !== "object") { errors.push(`${n} is not an object.`); return; }
    if (!s.concept) errors.push(`${n} has no "concept".`);
    if (typeof s.start !== "number" || typeof s.end !== "number" || s.end <= s.start) errors.push(`${n}: "start" and "end" must be numbers, with end after start.`);
    if (!s.summary) errors.push(`${n} has no "summary".`);
    const qs = Array.isArray(s.questions) ? s.questions : [];
    if (qs.length !== 2) errors.push(`${n} must have exactly 2 questions (it has ${qs.length}).`);
    qs.forEach((q, k) => {
      if (!Array.isArray(q.options) || q.options.length !== 4) errors.push(`${n}, question ${k + 1}: needs exactly 4 options.`);
      if (!Number.isInteger(q.answer) || q.answer < 0 || q.answer > 3) errors.push(`${n}, question ${k + 1}: "answer" must be 0, 1, 2 or 3.`);
    });
  });
  if (errors.length) {
    return {
      ok: false, snippets_count: 0, errors,
      fix_message: "Your answer had these problems:\n" + errors.map((e) => `- ${e}`).join("\n") +
        "\n\nSend the corrected JSON only, in one json code block, with all snippets (not just the fixed ones).",
    };
  }
  return { ok: true, snippets_count: items.length, errors: [], fix_message: "" };
}

routes.push(
  ["GET", /^\/api\/ai-status$/, () => ({ mode: settings.mode, provider: settings.provider, preset: presetOf(settings), model: settings.model, can_generate: canGenerate(), coverage_check: canGenerate() })],
  ["GET", /^\/api\/lectures\/([^/]+)\/generate\/estimate$/, async () => {
    await sleep(350);
    const chars = MOCK_SNIPPETS.reduce((a, s) => a + s.text.length, 0) + 6000; // transcript + instructions
    const input_tokens = Math.round(chars / 4), output_tokens = 900 * MOCK_SNIPPETS.length;
    // anthropic/claude-haiku-5.5 via OpenRouter: about $0.10 / $0.50 per million tokens
    const est_cost_usd = Math.round((input_tokens * 0.1e-6 + output_tokens * 0.5e-6) * 10000) / 10000;
    return { calls: 1, input_tokens, output_tokens, est_cost_usd };
  }],
  ["GET", /^\/api\/settings$/, () => publicSettings()],
  ["POST", /^\/api\/settings$/, async (_, body) => {
    await sleep(250);
    const { api_key, ...rest } = body;
    settings = { ...settings, ...rest };
    if (api_key) settings.api_key = api_key;
    persistSettings();
    return publicSettings();
  }],
  ["POST", /^\/api\/settings\/test$/, async () => {
    await sleep(900 + Math.random() * 600);
    if (settings.mode !== "api") return { ok: false, message: "API mode is off. Choose “Use an API” first.", latency_ms: 0 };
    if (!settings.model) return { ok: false, message: "No model set. Pick or type a model name.", latency_ms: 0 };
    if (settings.provider === "openai_compatible") {
      if (!settings.base_url) return { ok: false, message: "No base URL. For Ollama use http://localhost:11434/v1.", latency_ms: 0 };
      if (/localhost:1234/.test(settings.base_url)) return { ok: false, message: "Can't reach http://localhost:1234/v1. Is LM Studio's local server running?", latency_ms: 0 };
    } else if (!settings.api_key) return { ok: false, message: "No API key saved for this provider.", latency_ms: 0 };
    if (/bad|wrong/i.test(settings.api_key)) return { ok: false, message: "The provider rejected the key (401 Unauthorized). Check that you copied the whole key.", latency_ms: 412 };
    return { ok: true, message: `Connected to ${settings.model}. It answered with valid JSON.`, latency_ms: 640 + Math.round(Math.random() * 500) };
  }],
  ["GET", /^\/api\/lectures\/([^/]+)\/prompt$/, async () => { await sleep(300); return { parts: mockPromptParts() }; }],
  ["POST", /^\/api\/lectures\/([^/]+)\/import$/, async (_, body) => { await sleep(700); return mockImport(body.raw_text || ""); }],
  ["POST", /^\/api\/lectures\/([^/]+)\/generate$/, async (m) => {
    if (!canGenerate()) return { ok: false, snippets_count: 0, error: "API mode isn't set up. Save a provider, model and key first." };
    genStatus[decodeURIComponent(m[1])] = { started: Date.now() };
    return { ok: true, snippets_count: 0, error: null };
  }],
  ["GET", /^\/api\/lectures\/([^/]+)\/generate\/status$/, (m) => {
    const g = genStatus[decodeURIComponent(m[1])];
    if (!g) return { state: "idle", message: "" };
    const t = (Date.now() - g.started) / 1000;
    if (t > 7) return { state: "done", message: `${MOCK_SNIPPETS.length} snippets saved.` };
    return { state: "running", message: t < 2 ? "Reading the transcript…" : t < 5 ? "Asking the AI to cut the lecture into moments…" : "Checking cuts and questions…" };
  }],
);

// Minimal zip writer (stored, no compression) so "Download package" gives a real zip in mock mode.
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
function crc32(bytes) { let c = 0xffffffff; for (const x of bytes) c = CRC_TABLE[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
function zip(files) {
  const te = new TextEncoder(); const parts = []; const central = []; let offset = 0;
  const names = Object.keys(files);
  for (const name of names) {
    const nb = te.encode(name), data = te.encode(files[name]), crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true); local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true); local.setUint32(22, data.length, true); local.setUint16(26, nb.length, true);
    parts.push(local, nb, data);
    const cen = new DataView(new ArrayBuffer(46));
    cen.setUint32(0, 0x02014b50, true); cen.setUint16(4, 20, true); cen.setUint16(6, 20, true); cen.setUint16(8, 0x0800, true);
    cen.setUint32(16, crc, true); cen.setUint32(20, data.length, true); cen.setUint32(24, data.length, true);
    cen.setUint16(28, nb.length, true); cen.setUint32(42, offset, true);
    central.push(cen, nb);
    offset += 30 + nb.length + data.length;
  }
  const size = central.reduce((a, p) => a + p.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, names.length, true); end.setUint16(10, names.length, true);
  end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/zip" });
}

export async function mockPackage() {
  const files = {
    "START-HERE.md": `# Make snippets for “${MOCK_META.title}” with any AI chat\n\n` +
      "1. Open ChatGPT, Gemini or Claude in your browser.\n2. Paste PROMPT-part-1.md and send it.\n" +
      "3. Do the same for each next part, in the same chat.\n4. Copy all the AI's replies into one text file and save it.\n" +
      "5. In Rewind → AI settings, paste or upload that file and click Import.\n\n" +
      "Using a coding agent (Claude Code, Codex, Gemini CLI)? Open AGENT.md instead.\n\n(Demo package made in mock mode.)\n",
    "AGENT.md": "Read transcript.json and the PROMPT files, write raw_llm_output.json with all snippets, then run the import command and fix errors until it passes.\n",
    "meta.json": JSON.stringify(MOCK_META, null, 2),
  };
  mockPromptParts().forEach((p, i) => { files[`PROMPT-part-${i + 1}.md`] = p; });
  return zip(files);
}

// ---------------------------------------------------------------- round 5: slides, slide search, student chat

// Fake slide deck for demo01, drawn as SVG so mock mode needs no image files.
const MOCK_SLIDES = [
  { n: 1, title: "18.06 Linear Algebra · Lecture 1", text: "The geometry of linear equations. Gilbert Strang." },
  { n: 2, title: "Three ways to see Ax = b", text: "Row picture: one equation at a time, lines meet. Column picture: combinations of the columns. Matrix form: A times x equals b." },
  { n: 3, title: "Example: 2 equations, 2 unknowns", text: "2x − y = 0 and −x + 2y = 3. Coefficient matrix A = [2 −1; −1 2], unknowns (x, y), right-hand side b = (0, 3)." },
  { n: 4, title: "Matrix form", text: "A is the matrix of coefficients, x is the vector of unknowns, b is the right-hand side. Linear equations are Ax = b." },
  { n: 5, title: "Row picture", text: "Each equation is a line in the xy plane. The solution is where the lines meet: x = 1, y = 2." },
  { n: 6, title: "Next time: elimination", text: "Gaussian elimination solves Ax = b step by step: subtract multiples of one row from another until the system is triangular." },
];
const SNIPPET_SLIDES = { "demo01-001": [2], "demo01-002": [3, 4], "demo01-003": [4] };

function slideSvg(s) {
  const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const words = s.text.split(" ");
  const lines = [];
  for (const w of words) {
    const last = lines[lines.length - 1];
    if (last && (last + " " + w).length <= 48) lines[lines.length - 1] = last + " " + w; else lines.push(w);
  }
  const body = lines.slice(0, 7).map((l, i) => `<text x="80" y="${300 + i * 58}" font-size="34" fill="#474b63" font-family="Inter, sans-serif">${esc(l)}</text>`).join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#fff"/>` +
    `<rect x="0" y="0" width="1280" height="14" fill="#4f46e5"/>` +
    `<text x="80" y="170" font-size="58" font-family="Georgia, serif" fill="#15172b">${esc(s.title)}</text>` +
    `<rect x="80" y="205" width="120" height="5" rx="2" fill="#4f46e5"/>${body}` +
    `<text x="1200" y="680" font-size="26" text-anchor="end" fill="#7a7f96" font-family="Inter, sans-serif">${s.n} / ${MOCK_SLIDES.length}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
const slideOut = (s) => ({ n: s.n, title: s.title, text: s.text, image: slideSvg(s), image_url: slideSvg(s) });
const slideRef = (n, score) => {
  const s = MOCK_SLIDES.find((x) => x.n === n);
  return { lecture_id: MOCK_META.lecture_id, n, score, title: s.title, image_url: slideSvg(s) };
};

// Questions only the slides answer.
const SLIDE_ONLY = [{ re: /elimination|gauss|triangular/i, n: 6 }];

// Wrap the existing /api/ask mock: add slides, "slide_only", and the "slides" field on snippets.
{
  const i = routes.findIndex(([m, re]) => m === "POST" && re.source.includes("ask"));
  const orig = routes[i][2];
  routes[i] = ["POST", routes[i][1], async (match, body) => {
    const slideOnly = SLIDE_ONLY.find((x) => x.re.test(body.question || ""));
    const res = await orig(match, body);
    const withSlides = (snip) => (snip ? { ...snip, slides: SNIPPET_SLIDES[snip.id] || [] } : snip);
    if (slideOnly && res.status !== "found") {
      return { ...res, status: "slide_only", snippet: null, lecture: LECTURE, alternatives: [], slides: [slideRef(slideOnly.n, 0.61)] };
    }
    if (res.status === "found") {
      return {
        ...res, snippet: withSlides(res.snippet),
        alternatives: res.alternatives.map((a) => ({ ...a, snippet: withSlides(a.snippet) })),
        slides: (SNIPPET_SLIDES[res.snippet.id] || []).map((n, k) => slideRef(n, 0.72 - k * 0.08)),
      };
    }
    return { ...res, slides: [] };
  }];
}

// Settings gain chat_policy; ai-status gains chat_available.
settings.chat_policy ||= "strict";
{
  const i = routes.findIndex(([m, re]) => m === "GET" && re.source.includes("ai-status"));
  const orig = routes[i][2];
  routes[i] = ["GET", routes[i][1], (...a) => ({ ...orig(...a), chat_available: canGenerate() })];
}

// Lecturer stats gain "followups".
const FOLLOWUPS = { "demo01-001": 6, "demo01-002": 17, "demo01-003": 4 };
{
  const i = routes.findIndex(([m, re]) => m === "GET" && re.source.includes("lecturer\\/stats"));
  const orig = routes[i][2];
  routes[i] = ["GET", routes[i][1], (...a) => {
    const r = orig(...a);
    r.snippets = r.snippets.map((s) => ({ ...s, followups: FOLLOWUPS[s.snippet_id] || 0 }));
    return r;
  }];
}

let uploadedSlides = { [MOCK_META.lecture_id]: true };
routes.push(
  ["GET", /^\/api\/lectures\/([^/]+)\/slides$/, (m) => {
    const id = decodeURIComponent(m[1]);
    if (id !== MOCK_META.lecture_id || !uploadedSlides[id]) { const e = new Error("No slides for this lecture"); e.status = 404; throw e; }
    return MOCK_SLIDES.map(slideOut);
  }],
  ["POST", /^\/api\/lectures\/([^/]+)\/slides$/, async (m, body) => {
    await sleep(1600);
    if (body?.name && !/\.(pdf|pptx)$/i.test(body.name)) {
      const e = new Error("Unsupported file type. Upload a PDF or PPTX."); e.status = 400; throw e;
    }
    uploadedSlides[decodeURIComponent(m[1])] = true;
    return { count: MOCK_SLIDES.length, aligned: Object.keys(SNIPPET_SLIDES).length };
  }],
);

const fmtT = (t) => `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
const OUTSIDE = /eigen|determinant|history|who invented|calculus|python|matlab|quantum/i;

/** Fake streamed reply for POST /api/chat: yields the same events as rewind.chat.respond. */
export async function* mockChat(body, signal) {
  await sleep(500);
  if (signal?.aborted) return;
  const snip = MOCK_SNIPPETS.find((s) => s.id === body.snippet_id) || MOCK_SNIPPETS[0];
  const last = [...(body.messages || [])].reverse().find((m) => m.role === "user")?.content || "";
  const homework = HOMEWORK.test(last);
  const outside = !homework && OUTSIDE.test(last);
  const slides = SNIPPET_SLIDES[snip.id] || [];
  const mid = snip.start + Math.round((snip.end - snip.start) / 2);
  let text;
  const citations = [];
  if (outside && settings.chat_policy === "labelled") {
    text = "Not from your course materials: an eigenvalue is a number λ for which Ax = λx has a non-zero solution x. Your professor hasn't covered this in the recorded lectures yet, so check with them.";
  } else if (outside) {
    text = "This isn't in your course materials. I've flagged it to your lecturer so they can cover it.";
  } else if (homework) {
    text = `I won't solve it for you, but here's a hint: start from how the professor sets up the coefficient matrix [${fmtT(snip.start + 20)}]. Which numbers go in the first row, and why?`;
    citations.push({ kind: "time", lecture_id: snip.lecture_id, t: snip.start + 20 });
  } else {
    const n = slides[0] || 4;
    text = `Good question. The professor writes the whole system as one equation, Ax = b [${fmtT(snip.start + 8)}]: A holds the coefficients, x the unknowns and b the right-hand sides [Slide ${n}]. ` +
      `He says he "couldn't resist" writing it this way before drawing any pictures, because that's how he always thinks about a system [${fmtT(mid)}]. ` +
      `Right after this he draws the row picture for the same example [${fmtT(snip.end + 6)}].`;
    citations.push({ kind: "time", lecture_id: snip.lecture_id, t: snip.start + 8 }, { kind: "slide", lecture_id: snip.lecture_id, n },
      { kind: "time", lecture_id: snip.lecture_id, t: mid }, { kind: "time", lecture_id: snip.lecture_id, t: Math.floor(snip.end + 6) });
  }
  for (let i = 0; i < text.length;) {
    if (signal?.aborted) return;
    const step = 2 + Math.floor(Math.random() * 5);
    yield { type: "text", text: text.slice(i, i + step) };
    i += step;
    await sleep(18 + Math.random() * 22);
  }
  if (signal?.aborted) return;
  FOLLOWUPS[snip.id] = (FOLLOWUPS[snip.id] || 0) + 1;
  if (outside) state.gaps.unshift({ gap_id: Date.now(), question: last.toLowerCase(), count: 1, last_asked: new Date().toISOString() });
  yield { type: "done", citations, outside_materials: outside, homework };
}
