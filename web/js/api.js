// API client. Talks to api.py (same origin, or ?api=http://host:port),
// or to the in-browser mock when ?mock=1 or /api/health is unreachable.
import { mockFetch } from "./mock.js";

const params = new URLSearchParams(location.search);
const BASE = (params.get("api") || "").replace(/\/$/, "");
let modePromise = null;

async function detectMode() {
  if (params.get("mock") === "1") return { mocked: true, mode: "fake" };
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const r = await fetch(`${BASE}/api/health`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!r.ok) throw new Error(r.status);
    const h = await r.json();
    return { mocked: false, mode: h.mode || "real" };
  } catch {
    return { mocked: true, mode: "fake" };
  }
}

/** {mocked: bool, mode: "real"|"fake"} — "fake" also means the server is on demo data. */
export function getMode() {
  return (modePromise ||= detectMode());
}

async function call(method, path, body) {
  const { mocked } = await getMode();
  if (mocked) return mockFetch(method, path, body);
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} failed: ${r.status}`);
  return r.json();
}

const enc = encodeURIComponent;
export const api = {
  lectures: () => call("GET", "/api/lectures"),
  examples: () => call("GET", "/api/examples"),
  ask: (student_id, question) => call("POST", "/api/ask", { student_id, question }),
  answer: (student_id, snippet_id, question_index, chosen) =>
    call("POST", "/api/answer", { student_id, snippet_id, question_index, chosen }),
  rewatch: (student_id) => call("GET", `/api/students/${enc(student_id)}/rewatch`),
  stats: () => call("GET", "/api/lecturer/stats"),
  gaps: () => call("GET", "/api/lecturer/gaps"),
  resolveGap: (gap_id) => call("POST", `/api/lecturer/gaps/${enc(gap_id)}/resolve`),
  seed: () => call("POST", "/api/lecturer/seed"),
  graph: () => call("GET", "/api/graph"),
};

/** Resolve a lecture's file video_url (e.g. "media/x/video.mp4") against the API base. */
export function mediaUrl(url) {
  if (/^https?:/i.test(url)) return url;
  return `${BASE}/${String(url).replace(/^\//, "")}`;
}
