// Guided tour: dims the page, rings one element at a time and explains it.
// Starts by itself on the first visit of each page, and again from the "?" button in the header.
const SEEN = "rewind.guideSeen.";

const TOURS = {
  "page-student": [
    ["#ask-form", "Ask in your own words", "Type what you didn't get and press Rewind. You get the 1–2 minutes where your professor explains it, then two quick questions."],
    ["#lec-list", "Your lectures", "Every recording your professors added. Open one to see its chapters."],
    ["#chapters", "Chapters", "The lecture cut into short moments, with their minutes. Click one to watch it."],
    [".help", "This guide", "Press the question mark any time to see these tips again."],
  ],
  "page-lecturer": [
    ["#upload", "Add a recording", "Choose a video or audio file and give it a title. Rewind writes down what was said, cuts it into short moments and makes check questions. A one-hour lecture takes several minutes."],
    ["#metrics", "The numbers", "How many questions were asked, how many a clip answered, and how often students failed the check."],
    ["#stuck", "Where students get stuck", "Moments ranked by how often they were asked. Red means many wrong answers. Click a row to play it."],
    ["#gaps", "Questions for you", "Questions your recordings don't answer, and questions students sent you directly. Mark one as resolved once you have covered it."],
    ["#graph", "Where in each lecture", "One bar per lecture. Each block is a moment: darker means asked more often, red means many wrong answers."],
    [".help", "This guide", "Press the question mark any time to see these tips again."],
  ],
};

const pageKey = () => Object.keys(TOURS).find((k) => document.body.classList.contains(k));
let open = false;

export function startGuide() {
  const key = pageKey();
  if (!key || open) return;
  const steps = TOURS[key].map(([sel, title, text]) => ({ el: document.querySelector(sel), title, text }))
    .filter((s) => s.el && s.el.offsetParent !== null);
  if (!steps.length) return;
  open = true;
  try { localStorage.setItem(SEEN + key, "1"); } catch {}

  const ring = document.createElement("div"); ring.className = "guide-ring";
  const card = document.createElement("div"); card.className = "guide-card"; card.setAttribute("role", "dialog"); card.setAttribute("aria-live", "polite");
  const shield = document.createElement("div"); shield.className = "guide-shield";
  document.body.append(shield, ring, card);
  let i = 0;

  const close = () => { open = false; ring.remove(); card.remove(); shield.remove(); removeEventListener("resize", place); removeEventListener("scroll", place, true); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); if (e.key === "ArrowRight" || e.key === "Enter") go(1); if (e.key === "ArrowLeft") go(-1); };
  const go = (d) => { i += d; if (i >= steps.length) return close(); if (i < 0) i = 0; show(); };

  function place() {
    const r = steps[i].el.getBoundingClientRect(), pad = 8;
    Object.assign(ring.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
    const cw = Math.min(380, innerWidth - 24), ch = card.offsetHeight;
    const below = r.bottom + pad + 14 + ch < innerHeight;
    const top = below ? r.bottom + pad + 14 : Math.max(12, r.top - pad - 14 - ch);
    const left = Math.min(Math.max(12, r.left), innerWidth - cw - 12);
    Object.assign(card.style, { width: `${cw}px`, left: `${left}px`, top: `${top}px` });
  }

  function show() {
    const s = steps[i];
    card.innerHTML = "";
    const count = document.createElement("p"); count.className = "guide-count"; count.textContent = `Tip ${i + 1} of ${steps.length}`;
    const h = document.createElement("h3"); h.className = "guide-title"; h.textContent = s.title;
    const p = document.createElement("p"); p.className = "guide-text"; p.textContent = s.text;
    const row = document.createElement("div"); row.className = "guide-row";
    const skip = document.createElement("button"); skip.type = "button"; skip.className = "guide-skip"; skip.textContent = "Skip"; skip.onclick = close;
    const back = document.createElement("button"); back.type = "button"; back.className = "guide-skip"; back.textContent = "Back"; back.onclick = () => go(-1); back.hidden = i === 0;
    const next = document.createElement("button"); next.type = "button"; next.className = "btn btn-primary guide-next"; next.textContent = i === steps.length - 1 ? "Got it" : "Next"; next.onclick = () => go(1);
    row.append(skip, back, next); card.append(count, h, p, row);
    s.el.scrollIntoView({ block: "center", behavior: "instant" });
    place(); next.focus({ preventScroll: true });
  }

  shield.onclick = close;
  addEventListener("resize", place); addEventListener("scroll", place, true); document.addEventListener("keydown", onKey);
  show();
}

/** Called once by the header: starts the tour on a first visit, after the page has filled in. */
export function autoGuide() {
  const key = pageKey();
  let seen = true;
  try { seen = !!localStorage.getItem(SEEN + key); } catch {}
  if (key && !seen) setTimeout(() => { if (!document.querySelector(".modal-back")) startGuide(); }, 1400);
}
