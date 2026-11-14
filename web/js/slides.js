// Professor's slides in the student view: the slide next to a clip, a lightbox,
// and the "covered in the slides, not in the recording" card.
// Inputs: /api/lectures/{id}/slides -> [{n, title, text, image}] and the "slides" refs in /api/ask.
import { api } from "./api.js";
import { h, icon, lectureName, modal, toast } from "./ui.js";

const cache = new Map(); // lecture_id -> Promise<slides[]>
const params = new URLSearchParams(location.search);
const BASE = (params.get("api") || "").replace(/\/$/, "");

/** All slides of a lecture ([] if it has none). Cached. */
export function loadSlides(lectureId) {
  if (!cache.has(lectureId)) cache.set(lectureId, api.slides(lectureId).then((s) => (Array.isArray(s) ? s : [])).catch(() => []));
  return cache.get(lectureId);
}

/** Image URL for a slide: data:/http URLs as is, "slides/x.png" -> media/<id>/slides/x.png. */
export function slideImage(lectureId, s) {
  const src = s?.image_url || s?.image || s?.url || "";
  if (!src) return "";
  if (/^(https?:|data:|blob:)/i.test(src)) return src;
  const path = src.replace(/^\//, "");
  const rel = /^(media|api)\//.test(path) ? path : `media/${lectureId}/${path}`;
  return `${BASE}/${rel}`;
}

/** Slides for a clip: refs from /api/ask (with title/image) or the snippet's slide numbers. */
async function resolve(lecture, refs = [], nums = []) {
  const id = lecture.lecture_id;
  const wanted = refs.length ? refs.filter((r) => !r.lecture_id || r.lecture_id === id).map((r) => r.n) : nums;
  if (!wanted.length) return [];
  const deck = await loadSlides(id);
  return wanted.map((n) => {
    const ref = refs.find((r) => r.n === n) || {};
    const full = deck.find((s) => s.n === n) || {};
    return { n, title: ref.title || full.title || `Slide ${n}`, text: full.text || "", image: slideImage(id, ref.image_url || ref.image ? ref : full) };
  }).filter((s) => s.image);
}

/** Lightbox with prev/next through the lecture's deck. */
export async function openSlide(lecture, n) {
  const deck = (await loadSlides(lecture.lecture_id)).filter((s) => slideImage(lecture.lecture_id, s));
  if (!deck.length) { toast("Slide image isn't available."); return; }
  let i = Math.max(0, deck.findIndex((s) => s.n === n));
  const img = h("img", { class: "sl-big", alt: "" });
  const cap = h("p", { class: "sl-cap" });
  const prev = h("button", { class: "sl-nav sl-prev", "aria-label": "Previous slide" }, icon("arrow"));
  const next = h("button", { class: "sl-nav sl-next", "aria-label": "Next slide" }, icon("arrow"));
  const show = () => {
    const s = deck[i];
    img.src = slideImage(lecture.lecture_id, s);
    img.alt = s.title || `Slide ${s.n}`;
    cap.replaceChildren(h("strong", {}, `Slide ${s.n}`), s.title ? ` · ${s.title}` : "", h("span", { class: "muted" }, ` · ${lectureName(lecture)}`));
    prev.disabled = i === 0;
    next.disabled = i === deck.length - 1;
  };
  prev.addEventListener("click", () => { if (i > 0) { i--; show(); } });
  next.addEventListener("click", () => { if (i < deck.length - 1) { i++; show(); } });
  const onKey = (e) => {
    if (e.key === "ArrowLeft") prev.click();
    if (e.key === "ArrowRight") next.click();
  };
  document.addEventListener("keydown", onKey);
  modal(h("div", { class: "sl-box" }, h("div", { class: "sl-stage" }, img, prev, next), cap),
    { cls: "modal-slide", onClose: () => document.removeEventListener("keydown", onKey) });
  show();
}

/** The slide shown next to a clip. Returns an element that fills in (or hides) once slides load. */
export function slideAside(lecture, refs, nums) {
  const box = h("aside", { class: "sl-aside", hidden: true });
  resolve(lecture, refs, nums).then((slides) => {
    if (!slides.length) return;
    let k = 0;
    const img = h("img", { class: "sl-thumb", alt: "" });
    const title = h("p", { class: "sl-title" });
    const btn = h("button", { class: "sl-open", type: "button", "aria-label": "Enlarge slide" }, img, h("span", { class: "sl-zoom" }, icon("search")));
    const tabs = slides.length > 1
      ? h("div", { class: "sl-tabs" }, ...slides.map((s, j) => h("button", { class: `sl-tab ${j === 0 ? "is-on" : ""}`, type: "button", onclick: () => { k = j; draw(); } }, String(s.n))))
      : null;
    const draw = () => {
      const s = slides[k];
      img.src = s.image;
      img.alt = s.title;
      title.replaceChildren(h("strong", {}, `Slide ${s.n}`), ` · ${s.title}`);
      tabs?.querySelectorAll(".sl-tab").forEach((t, j) => t.classList.toggle("is-on", j === k));
    };
    btn.addEventListener("click", () => openSlide(lecture, slides[k].n));
    box.replaceChildren(h("p", { class: "sl-label" }, icon("quote"), "On the professor's slide"), btn, h("div", { class: "sl-meta" }, title, tabs));
    draw();
    box.hidden = false;
    box.closest(".clip-media")?.classList.add("has-slide");
  });
  return box;
}

/** Card for status "slide_only": the answer is on a slide but not in any recorded clip. */
export function slideOnlyCard(res, question) {
  const lecture = res.lecture || {};
  const card = h("article", { class: "card result-card sl-only appear" },
    h("div", { class: "clip-head" },
      h("span", { class: "badge badge-accent" }, icon("quote"), "Your professor's own slides"),
      h("span", { class: "clip-when" }, lectureName(lecture))),
    h("h2", { class: "concept" }, "Covered in the slides, not in the recording"),
    h("p", { class: "muted" }, "Your professor has this on a slide but doesn't explain it in the recorded lecture. Here's the slide; if it's still unclear, ask them in class."),
    h("blockquote", { class: "nc-quote" }, `“${question}”`));
  const grid = h("div", { class: "sl-only-grid" });
  card.append(grid);
  resolve(lecture, res.slides || [], []).then((slides) => {
    if (!slides.length) { grid.replaceChildren(h("p", { class: "muted" }, "The slide image isn't available.")); return; }
    grid.replaceChildren(...slides.map((s) => h("button", { class: "sl-only-item", type: "button", onclick: () => openSlide(lecture, s.n) },
      h("img", { src: s.image, alt: s.title }),
      h("span", { class: "sl-title" }, h("strong", {}, `Slide ${s.n}`), ` · ${s.title}`))));
  });
  return card;
}

