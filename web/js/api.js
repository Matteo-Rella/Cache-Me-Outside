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
  if (!r.ok) {
    let detail = "";
    try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : j.message || j.error || ""; } catch {}
    const err = new Error(detail || `${method} ${path} failed: ${r.status}`);
    err.status = r.status;
    throw err;
  }
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
  askProfessor: (student_id, question, lecture_id) => call("POST", "/api/questions", { student_id, question, lecture_id }),
  uploadStatus: (job_id) => call("GET", `/api/lecturer/uploads/${enc(job_id)}`),
  /** Upload a recording (FormData with file, title, course). Returns the job to poll. */
  addLecture: async (form) => {
    const { mocked } = await getMode();
    if (mocked) return mockFetch("POST", "/api/lecturer/lectures", { title: form.get("title") });
    const r = await fetch(`${BASE}/api/lecturer/lectures`, { method: "POST", body: form });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `upload failed: ${r.status}`);
    return r.json();
  },
  // AI settings (local-only on the server)
  aiStatus: () => call("GET", "/api/ai-status"),
  settings: () => call("GET", "/api/settings"),
  saveSettings: (s) => call("POST", "/api/settings", s),
  testConnection: () => call("POST", "/api/settings/test"),
  prompt: (id) => call("GET", `/api/lectures/${enc(id)}/prompt`),
  importAnswer: (id, raw_text) => call("POST", `/api/lectures/${enc(id)}/import`, { raw_text }),
  generate: (id) => call("POST", `/api/lectures/${enc(id)}/generate`),
  generateStatus: (id) => call("GET", `/api/lectures/${enc(id)}/generate/status`),
  generateEstimate: (id) => call("GET", `/api/lectures/${enc(id)}/generate/estimate`),
};

// ---------------------------------------------------------------- round 5: slides and chat

api.slides = (id) => call("GET", `/api/lectures/${enc(id)}/slides`);

/** Upload a slide deck (PDF/PPTX) for a lecture. Local-only on the server (403 otherwise). */
api.uploadSlides = async (id, file) => {
  const { mocked } = await getMode();
  if (mocked) return mockFetch("POST", `/api/lectures/${enc(id)}/slides`, { name: file.name });
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`${BASE}/api/lectures/${enc(id)}/slides`, { method: "POST", body: form });
  if (!r.ok) {
    let detail = "";
    try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : ""; } catch {}
    const err = new Error(detail || `Upload failed: ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
};

/**
 * Follow-up chat. POST /api/chat answers with text/event-stream; each event is one JSON object:
 * {type: "text", text} chunks, then {type: "done", citations, outside_materials, homework}.
 * Calls onEvent for each one. Rejects with err.status (409 = chat not available).
 */
export async function chatStream(body, onEvent, signal) {
  const { mocked } = await getMode();
  if (mocked) {
    const { mockChat } = await import("./mock.js");
    for await (const ev of mockChat(body, signal)) {
      if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
      onEvent(ev);
    }
    if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
    return;
  }
  const r = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) {
    let detail = "";
    try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : ""; } catch {}
    const err = new Error(detail || `Chat failed: ${r.status}`);
    err.status = r.status;
    throw err;
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const flush = (block) => {
    // An SSE event: lines starting with "data:" (possibly several), separated by a blank line.
    const data = block.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
    if (!data || data === "[DONE]") return;
    try {
      onEvent(JSON.parse(data));
    } catch {
      if (data.trimStart().startsWith("{")) onEvent({ type: "error", message: "The reply stream was interrupted." });
      else onEvent({ type: "text", text: data });
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.search(/\r?\n\r?\n/)) !== -1) {
        flush(buf.slice(0, i));
        buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
      }
    }
    buf += decoder.decode();
    if (buf.trim()) flush(buf);
  } finally {
    try { reader.releaseLock(); } catch {}
  }
}

/** Download a lecture's context package (zip). Uses a real link, or a zip built by the mock. */
export async function downloadPackage(id) {
  const { mocked } = await getMode();
  const a = document.createElement("a");
  a.download = `rewind-${id}-package.zip`;
  if (mocked) {
    const { mockPackage } = await import("./mock.js");
    a.href = URL.createObjectURL(await mockPackage(id));
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } else {
    a.href = `${BASE}/api/lectures/${enc(id)}/package`;
  }
  document.body.append(a);
  a.click();
  a.remove();
}

/** Resolve a lecture's file video_url (e.g. "media/x/video.mp4") against the API base. */
export function mediaUrl(url) {
  if (/^https?:/i.test(url)) return url;
  return `${BASE}/${String(url).replace(/^\//, "")}`;
}
