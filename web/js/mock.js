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
