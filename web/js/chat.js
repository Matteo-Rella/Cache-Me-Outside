// "Ask a follow-up" chat under a clip. Streams the reply from POST /api/chat (SSE),
// turns [mm:ss] into chips that play the lecture from there and [Slide N] into chips
// that open the slide. History is kept per clip in memory; the last 10 messages are sent.
import { chatStream } from "./api.js";
import { h, icon } from "./ui.js";

const history = new Map(); // snippet_id -> [{role, content, meta?}]
const CITE = /\[(?:(\d{1,2}:\d{2}(?::\d{2})?)|Slide\s+(\d+))\]/gi;
const OUTSIDE_LABEL = /^\s*Not from your course materials:\s*/i;
const SUGGESTIONS = ["Explain it more simply", "Give me an example", "Why does this matter?"];

const toSeconds = (s) => s.split(":").map(Number).reduce((a, x) => a * 60 + x, 0);

/** Text with citations as chips. Before "done", every citation is a chip; after, only the validated ones. */
function rich(text, citations, { onTime, onSlide }) {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      const t = toSeconds(m[1]);
      const ok = !citations || citations.some((c) => c.kind === "time" && Math.abs(c.t - t) < 1.5);
      out.push(ok ? h("button", { class: "ct-chip ct-time", type: "button", title: `Play the lecture from ${m[1]}`, onclick: () => onTime(t) }, icon("play"), m[1]) : m[0]);
    } else {
      const n = Number(m[2]);
      const ok = !citations || citations.some((c) => c.kind === "slide" && c.n === n);
      out.push(ok ? h("button", { class: "ct-chip ct-slide", type: "button", title: `Open slide ${n}`, onclick: () => onSlide(n) }, `Slide ${n}`) : m[0]);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/**
 * @param {{snippet, lecture, studentId: () => string, onTime: (t:number)=>void, onSlide: (n:number)=>void}} o
 */
export function chatPanel({ snippet, lecture, studentId, onTime, onSlide }) {
  const msgs = history.get(snippet.id) || [];
  history.set(snippet.id, msgs);
  const log = h("div", { class: "ct-log", "aria-live": "polite" });
  const input = h("textarea", { class: "ct-input", rows: 1, placeholder: "Ask about this clip…", "aria-label": "Ask a follow-up" });
  const send = h("button", { class: "btn btn-primary ct-send", type: "submit", "aria-label": "Send" }, icon("send"));
  const form = h("form", { class: "ct-form" }, input, send);
  const suggest = h("div", { class: "ct-suggest" }, ...SUGGESTIONS.map((s) =>
    h("button", { class: "chip chip-sm", type: "button", onclick: () => submit(s) }, s)));
  const card = h("section", { class: "card ct-card appear delay" },
    h("div", { class: "ct-head" },
      h("div", {}, h("h3", {}, "Ask a follow-up"),
        h("p", { class: "muted small" }, `Answers come only from ${lecture.lecture_number ? `Lecture ${lecture.lecture_number}` : "this lecture"} and its slides, with links to the exact moment.`))),
    log, suggest, form);
  let busy = false;
  let controller = null;

  const fit = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 120)}px`; };
  input.addEventListener("input", fit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(input.value); });

  function bubble(m) {
    if (m.role === "user") return h("div", { class: "ct-msg ct-user" }, h("p", {}, m.content));
    const meta = m.meta || {};
    const outside = !!meta.outside_materials;
    let text = m.content;
    let label = null;
    if (OUTSIDE_LABEL.test(text)) { text = text.replace(OUTSIDE_LABEL, ""); label = "Not from your course materials"; }
    else if (outside) label = "Not in your course materials";
    const body = h("p", {}, ...rich(text, meta.done ? meta.citations || [] : null, { onTime, onSlide }));
    if (!meta.done) body.append(h("span", { class: "ct-caret" }));
    return h("div", { class: `ct-msg ct-ai ${outside ? "is-outside" : ""} ${meta.error ? "is-error" : ""}` },
      label ? h("span", { class: "ct-label" }, label) : null,
      meta.homework && meta.done && !meta.error ? h("span", { class: "ct-label ct-hint" }, icon("bulb"), "Hint, not a solution") : null,
      m.content || !meta.error ? (m.content ? body : h("span", { class: "ct-typing" }, h("i"), h("i"), h("i"))) : null,
      meta.error ? h("p", { class: "ct-err-note" }, meta.error) : null,
      meta.error ? h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => retry() }, icon("replay"), "Try again") : null,
      outside && meta.done && !meta.error ? h("span", { class: "ct-flag" }, icon("send"), "Sent to your lecturer") : null);
  }

  function render() {
    log.replaceChildren(...msgs.map(bubble));
    suggest.hidden = msgs.length > 0;
    log.hidden = msgs.length === 0;
  }

  function retry() {
    const lastUser = [...msgs].reverse().find((m) => m.role === "user");
    if (msgs.length && msgs[msgs.length - 1].role === "assistant") msgs.pop();
    if (lastUser) { msgs.splice(msgs.lastIndexOf(lastUser), 1); submit(lastUser.content); }
  }

  async function submit(text) {
    text = String(text || "").trim();
    if (!text || busy) return;
    busy = true; send.disabled = true;
    input.value = ""; fit();
    msgs.push({ role: "user", content: text });
    const reply = { role: "assistant", content: "", meta: { done: false } };
    msgs.push(reply);
    render();
    const payload = msgs.filter((m) => !m.meta?.error && !m.meta?.cancelled && m !== reply).slice(-10).map(({ role, content }) => ({ role, content }));
    controller = new AbortController();
    let receivedDone = false;
    try {
      await chatStream({ student_id: studentId(), snippet_id: snippet.id, messages: payload }, (ev) => {
        if (ev.type === "text") { reply.content += ev.text || ""; }
        else if (ev.type === "done") { receivedDone = true; reply.meta = { ...ev, done: true }; }
        else if (ev.type === "error") { receivedDone = true; reply.meta = { done: true, error: ev.message || "Something went wrong." }; }
        render();
      }, controller.signal);
      if (!receivedDone && !controller.signal.aborted) {
        reply.meta = { done: true, error: "The reply was interrupted before finishing." };
        render();
      }
    } catch (err) {
      if (err.name === "AbortError") {
        if (!reply.content) {
          const idx = msgs.indexOf(reply);
          if (idx !== -1) msgs.splice(idx, 1);
        } else {
          reply.meta = { done: true, cancelled: true, error: "Reply cancelled before finishing." };
        }
        render();
        return;
      }
      if (err.status === 409) {
        const idx = msgs.indexOf(reply);
        if (idx !== -1) msgs.splice(idx, 1);
        card.replaceChildren(h("div", { class: "ct-head" }, h("h3", {}, "Ask a follow-up"),
          h("p", { class: "muted small" }, "The follow-up chat isn't available right now. The clip and the questions below still work.")));
        return;
      }
      reply.content = reply.content || "Sorry, I couldn't answer that just now.";
      reply.meta = { done: true, error: err.message || "Network error." };
      render();
    } finally {
      busy = false; send.disabled = false; controller = null;
    }
  }

  card.abortChat = () => controller?.abort();
  render();
  return card;
}

