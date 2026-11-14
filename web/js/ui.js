// Small UI helpers shared by both pages: element builder, icons, time format,
// header, modal and toast.
import { getMode } from "./api.js";
import { autoGuide, startGuide } from "./guide.js";

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}

const PATHS = {
  play: '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/><rect x="13.5" y="5" width="4" height="14" rx="1.2" fill="currentColor" stroke="none"/>',
  replay: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/>',
  rewind: '<path d="M11 7 5 12l6 5V7Z" fill="currentColor"/><path d="M19 7l-6 5 6 5V7Z" fill="currentColor"/>',
  quote: '<path d="M9.5 7C6.5 7.8 5 10 5 13v4h5v-5H7.6c.2-1.6 1-2.6 2.4-3.1L9.5 7Zm8 0c-3 .8-4.5 3-4.5 6v4h5v-5h-2.4c.2-1.6 1-2.6 2.4-3.1L17.5 7Z" fill="currentColor" stroke="none"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
  send: '<path d="M4 12 20 4l-6 16-3-7-7-1Z"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3Z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/>',
  inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6l2-8Z"/>',
  graph: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="8" r="2.5"/><circle cx="9" cy="18" r="2.5"/><path d="M8 7.5 16 8M7 8.3 8.5 15.6M16.6 10 10.7 16.6"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
};

export function icon(name, cls = "") {
  const span = document.createElement("span");
  span.className = `icon ${cls}`;
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${PATHS[name] || ""}</svg>`;
  return span;
}

export function fmt(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${p(mm)}:${p(ss)}`;
}

export const pct = (x) => `${Math.round((x || 0) * 100)}%`;

/** "MIT 14.01 Principles of Microeconomics" -> "Microeconomics". */
export function subjectName(l) {
  const s = String(l?.course || "").trim()
    .replace(/^(MIT|Yale|Harvard|Stanford|UC\s?Berkeley|Open\s?Yale)\b[\s:|-]*/i, "")
    .replace(/^[A-Z]{0,6}\s?\d+(\.\d+)?[A-Z]?\b[\s:|-]*/, "")
    .replace(/^(principles of|introduction to|intro to|foundations of|fundamentals of)\s+/i, "");
  return s || l?.title || l?.lecture_id || "Lecture";
}

/** "Microeconomics · Lecture 1" (or the title when there's no number). */
export function lectureName(lecture) {
  const n = lecture?.lecture_number ? `Lecture ${lecture.lecture_number}` : lecture?.title || "Lecture";
  return lecture?.course ? `${subjectName(lecture)} · ${n}` : n;
}

/** "min 3–4": whole minutes of the lecture, so it does not read like a clock time. */
export function mins(start, end) {
  const a = Math.floor((start || 0) / 60), b = Math.floor((end || 0) / 60);
  return a === b ? `min ${a}` : `min ${a}–${b}`;
}

export function lectureLine(lecture, snippet) {
  return `${lectureName(lecture)} · ${mins(snippet.start, snippet.end)}`;
}

/** Shared header. `page` is "student" | "lecturer"; `extra` goes on the right. */
export function header(page, extra) {
  const mock = location.search.includes("mock=1") ? "?mock=1" : "";
  const link = (href, label, key) =>
    h("a", { href: href + mock, class: `nav-link ${page === key ? "is-active" : ""}`, "aria-current": page === key ? "page" : null }, label);
  const tag = h("span", { class: "demo-tag", hidden: true, title: "Running on built-in demo data, not the live API" }, "demo data");
  getMode().then(({ mocked, mode }) => { if (mocked || mode === "fake") tag.hidden = false; });
  autoGuide();
  return h("header", { class: "site-header" },
    h("div", { class: "wrap header-inner" },
      h("a", { class: "brand", href: "index.html" + mock },
        h("span", { class: "brand-mark" }, icon("rewind")),
        h("span", { class: "brand-name" }, "Rewind"),
        h("span", { class: "brand-tag" }, "Ask your lecture.")),
      tag,
      h("nav", { class: "nav" }, link("index.html", "Student", "student"), link("lecturer.html", "Lecturer", "lecturer"),
        // Lecturer-side only: students never see the settings link.
        page !== "student" ? link("settings.html", "AI settings", "settings") : null),
      extra || "",
      h("button", { class: "help", type: "button", "aria-label": "How to use this page", title: "How to use this page", onclick: () => startGuide() }, "?"),
      h("a", { class: "logout", href: "login.html" + mock, onclick: () => { try { localStorage.removeItem("rewind.role"); } catch {} } }, "Log out")));
}

/** Simple modal. Returns {el, close}. */
export function modal(content, { dismissable = true, cls = "", onClose } = {}) {
  const close = () => {
    back.classList.add("is-leaving");
    setTimeout(() => back.remove(), 180);
    document.removeEventListener("keydown", onKey);
    onClose?.();
  };
  const onKey = (e) => { if (e.key === "Escape" && dismissable) close(); };
  const box = h("div", { class: `modal ${cls}`, role: "dialog", "aria-modal": "true" },
    dismissable ? h("button", { class: "modal-x", "aria-label": "Close", onclick: close }, icon("x")) : null,
    content);
  const back = h("div", { class: "modal-back", onclick: (e) => { if (e.target === back && dismissable) close(); } }, box);
  document.body.append(back);
  document.addEventListener("keydown", onKey);
  return { el: box, close };
}

export function toast(text) {
  const t = h("div", { class: "toast" }, text);
  document.body.append(t);
  setTimeout(() => t.classList.add("is-leaving"), 2600);
  setTimeout(() => t.remove(), 3000);
}
