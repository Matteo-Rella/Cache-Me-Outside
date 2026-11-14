// Student page: ask a question, watch the professor's clip, check yourself.
import { api } from "./api.js";
import { ClipPlayer } from "./player.js";
import { fmt, h, header, icon, lectureLine, modal, subjectName } from "./ui.js";
import { chatPanel } from "./chat.js";
import { openSlide, slideAside, slideOnlyCard } from "./slides.js";

// Round 5: chat shows only when the server says it can (API mode).
const aiStatusP = api.aiStatus().catch(() => ({}));

const NAME_KEY = "rewind.studentName";
const $ = (s) => document.querySelector(s);
let player = null;
let askToken = 0;
let abortActiveChat = null;

const getName = () => { try { return localStorage.getItem(NAME_KEY) || ""; } catch { return ""; } };
const setName = (n) => { try { localStorage.setItem(NAME_KEY, n); } catch {} };
const studentId = () => getName().trim().toLowerCase() || "guest";

// ------------------------------------------------------------------ header + name

const userChip = h("button", { class: "user-chip", onclick: () => askName(true) }, icon("user"), h("span", {}, ""));
function renderUser() { userChip.querySelector("span:last-child").textContent = getName() || "Guest"; }
document.body.prepend(header("student", userChip));
renderUser();

function askName(dismissable = false) {
  const input = h("input", { class: "input", placeholder: "Your first name", maxlength: 40, value: getName(), autocomplete: "given-name" });
  const form = h("form", { class: "name-form" },
    h("div", { class: "modal-mark" }, icon("rewind")),
    h("h2", { class: "modal-title" }, getName() ? "Change your name" : "Welcome to Rewind"),
    h("p", { class: "muted" }, "Ask anything about your lectures and jump straight to the moment your professor explains it. What should we call you?"),
    input,
    h("button", { class: "btn btn-primary btn-block", type: "submit" }, "Start asking", icon("arrow")));
  const m = modal(form, { dismissable, cls: "modal-sm" });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const n = input.value.trim();
    if (!n) { input.focus(); input.classList.add("shake"); setTimeout(() => input.classList.remove("shake"), 400); return; }
    setName(n);
    renderUser();
    m.close();
    loadRewatch();
    $("#q").focus();
  });
  setTimeout(() => input.focus(), 60);
}

// ------------------------------------------------------------------ hero

const form = $("#ask-form");
const q = $("#q");
form.addEventListener("submit", (e) => { e.preventDefault(); ask(q.value); });
q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(q.value); } });
const fitQ = () => { q.style.height = "auto"; q.style.height = `${Math.min(q.scrollHeight, 140)}px`; };
q.addEventListener("input", fitQ);
addEventListener("resize", fitQ);

function searchLine(ls) {
  const names = [...new Set(ls.map(subjectName))];
  const n = ls.length;
  const head = `${n} lecture${n === 1 ? "" : "s"}`;
  return names.length <= 3 ? `${head}: ${names.join(" · ")}` : `${head}: ${names.slice(0, 2).join(" · ")} +${names.length - 2} more`;
}

let lectureLine_ = "1 lecture";
api.lectures().then((all) => {
  const ls = all.filter((l) => (l.snippet_count || 0) > 0);
  const moments = ls.reduce((a, l) => a + l.snippet_count, 0);
  lectureLine_ = searchLine(ls.length ? ls : all);
  const el = $("#coverage");
  el.textContent = `Searching ${lectureLine_}`;
  el.title = [`${moments} explained moments`,
    ...ls.map((l) => `${subjectName(l)} · ${l.lecture_number ? `Lecture ${l.lecture_number}: ` : ""}${l.title}`)].join("\n");
}).catch(() => {});

api.examples().then((ex) => {
  $("#examples").replaceChildren(...ex.slice(0, 3).map((t) =>
    h("button", { class: "chip", type: "button", onclick: () => { q.value = t; ask(t); } }, icon("sparkle"), t)));
}).catch(() => {});

// ------------------------------------------------------------------ ask flow

async function ask(text) {
  text = text.trim();
  if (!text) { q.focus(); return; }
  if (!getName()) { askName(); return; }
  const token = ++askToken;
  document.body.classList.add("has-result");
  q.value = text;
  requestAnimationFrame(fitQ);
  setTimeout(fitQ, 500); // again once the hero has collapsed
  abortActiveChat?.(); abortActiveChat = null;
  player?.destroy(); player = null;
  const out = $("#result");
  out.replaceChildren(loadingCard());
  window.scrollTo({ top: 0, behavior: "smooth" });
  let res;
  try {
    res = await api.ask(studentId(), text);
  } catch (err) {
    if (token !== askToken) return;
    out.replaceChildren(errorCard(err));
    return;
  }
  if (token !== askToken) return;
  if (res.status === "found" && res.snippet) out.replaceChildren(...foundView(res, text));
  else if (res.status === "slide_only" && res.slides?.length) out.replaceChildren(slideOnlyCard(res, text));
  else out.replaceChildren(notCoveredCard(text));
}

function loadingCard() {
  const code = h("span", { class: "rw-code" }, "45:00");
  let t = 45 * 60;
  const iv = setInterval(() => {
    if (!code.isConnected) return clearInterval(iv);
    t = t > 40 ? t - 37 - Math.floor(Math.random() * 20) : 45 * 60;
    code.textContent = fmt(t);
  }, 50);
  return h("div", { class: "card result-card is-loading", "aria-live": "polite" },
    h("div", { class: "rw-line" }, h("span", { class: "rw-icon" }, icon("rewind")), code,
      h("span", { class: "muted rw-searching" }, `Searching ${lectureLine_}…`)),
    h("div", { class: "skel skel-video" }),
    h("div", { class: "skel skel-line w60" }),
    h("div", { class: "skel skel-line w90" }),
    h("div", { class: "skel skel-line w75" }));
}

function errorCard(err) {
  return h("div", { class: "card result-card notice" },
    h("h3", {}, "Something went wrong"),
    h("p", { class: "muted" }, `We couldn't reach Rewind (${err.message}). Try again in a moment.`));
}

function notCoveredCard(text) {
  return h("div", { class: "card result-card notcovered appear" },
    h("div", { class: "nc-icon" }, icon("send")),
    h("div", {},
      h("h3", {}, "Not in the lectures yet"),
      h("p", {}, "Your professor hasn't covered this in the recorded lectures yet. We've sent your question to them."),
      h("blockquote", { class: "nc-quote" }, `“${text}”`),
      h("p", { class: "muted small" }, "Tip: try asking with the words your professor would use.")));
}

function foundView(res, text) {
  const { snippet, lecture, homework, alternatives = [] } = res;
  const playerMount = h("div", { class: "player-mount" });
  const slideBox = h("div", { class: "clip-slide" });
  const media = h("div", { class: "clip-media" }, playerMount, slideBox);
  const jumpBar = h("div", { class: "ct-jump", hidden: true });
  let current = { snip: snippet, lec: lecture };
  const clipHead = h("div", { class: "clip-head" });
  const clipInfo = h("div", { class: "clip-info" });

  const showClip = (snip, lec, label) => {
    player?.destroy();
    player = new ClipPlayer(playerMount, { lecture: lec, start: snip.start, end: snip.end });
    current = { snip, lec };
    jumpBar.hidden = true;
    media.classList.remove("has-slide");
    const refs = snip === snippet ? res.slides || [] : [];
    slideBox.replaceChildren(slideAside(lec, refs, snip.slides || []));
    clipHead.replaceChildren(...[
      h("span", { class: "badge badge-accent" }, icon("quote"), "Your professor's own words"),
      label ? h("span", { class: "badge" }, label) : null,
      h("span", { class: "clip-when" }, icon("clock"), lectureLine(lec, snip))].filter(Boolean));
    clipInfo.replaceChildren(
      h("h2", { class: "concept" }, snip.concept),
      h("p", { class: "lecture-title muted" }, lec.title),
      h("div", { class: "summary" }, h("span", { class: "summary-label" }, "In two lines"), h("p", {}, snip.summary)));
  };
  showClip(snippet, lecture);

  const clipCard = h("article", { class: "card result-card appear" },
    homework ? h("div", { class: "hw-note" }, icon("bulb"),
      h("span", {}, h("strong", {}, "Here's where the method is explained. "), "Try it yourself, then check below.")) : null,
    clipHead, jumpBar, media, clipInfo);

  // [mm:ss] chips in the chat: seek inside the clip, or play that part of the lecture.
  const onTimeFor = (snip, lec, t) => {
    if (!player || current.snip.id !== snip.id) return;
    if (t >= snip.start && t < snip.end) {
      if (player.start !== snip.start || player.end !== snip.end) player.setRange(snip.start, snip.end);
      player.seek(t); player.play();
      jumpBar.hidden = true;
    } else {
      player.setRange(t, t + 75);
      jumpBar.replaceChildren(icon("clock"), h("span", {}, `Playing ${fmt(t)} from ${lectureLine(lec, { start: t, end: t + 75 }).split(" · ").slice(0, -1).join(" · ")}`),
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => { player.setRange(snip.start, snip.end); jumpBar.hidden = true; } }, icon("replay"), "Back to the clip"));
      jumpBar.hidden = false;
    }
    media.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const chatSlot = h("div", { class: "ct-slot" });
  let chatEl = null;
  const mountChat = (snip, lec) => {
    chatEl?.abortChat?.();
    chatEl = null;
    chatSlot.replaceChildren();
    aiStatusP.then((s) => {
      if (!chatSlot.isConnected || current.snip.id !== snip.id) return;
      if (s && s.chat_available) {
        chatEl = chatPanel({
          snippet: snip,
          lecture: lec,
          studentId,
          onTime: (t) => onTimeFor(snip, lec, t),
          onSlide: (n) => openSlide(lec, n),
        });
        abortActiveChat = () => chatEl?.abortChat?.();
        chatSlot.replaceChildren(chatEl);
      }
    });
  };
  mountChat(snippet, lecture);

  const quiz = quizCard(snippet, alternatives, (alt) => {
    showClip(alt.snippet, alt.lecture, "Explained another way");
    mountChat(alt.snippet, alt.lecture);
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  return [clipCard, chatSlot, quiz];
}

// ------------------------------------------------------------------ quiz

function quizCard(snippet, alternatives, playAlt) {
  const body = h("div", { class: "quiz-body" });
  const steps = h("div", { class: "quiz-steps" });
  const card = h("section", { class: "card quiz-card appear delay" },
    h("div", { class: "quiz-head" }, h("h3", {}, "Check yourself"), steps), body);
  const qs = snippet.questions || [];
  const results = [];
  let altOffered = false;

  const renderSteps = (i) => steps.replaceChildren(...qs.map((_, k) =>
    h("span", { class: `step ${k === i ? "is-current" : ""} ${results[k] === true ? "is-right" : results[k] === false ? "is-wrong" : ""}` })));

  const show = (i) => {
    renderSteps(i);
    if (i >= qs.length) return finish();
    const qq = qs[i];
    const feedback = h("div", { class: "feedback" });
    const next = h("div", { class: "quiz-next" });
    const opts = qq.options.map((o, k) => h("button", { class: "option", type: "button", onclick: () => choose(k) },
      h("span", { class: "option-key" }, "ABCD"[k]), h("span", {}, o)));
    body.replaceChildren(
      h("p", { class: "quiz-count muted" }, `Question ${i + 1} of ${qs.length}`),
      h("p", { class: "quiz-q" }, qq.q),
      h("div", { class: "options" }, ...opts), feedback, next);

    async function choose(k) {
      opts.forEach((b) => (b.disabled = true));
      opts[k].classList.add("is-chosen");
      let r;
      try { r = await api.answer(studentId(), snippet.id, i, k); }
      catch (err) { feedback.replaceChildren(h("p", { class: "muted" }, `Couldn't check that answer (${err.message}).`)); opts.forEach((b) => (b.disabled = false)); return; }
      results[i] = r.correct;
      renderSteps(i);
      opts[r.correct_index]?.classList.add("is-right");
      if (!r.correct) opts[k].classList.add("is-wrong");
      feedback.replaceChildren(h("div", { class: `fb ${r.correct ? "fb-right" : "fb-wrong"}` },
        h("span", { class: "fb-icon" }, icon(r.correct ? "check" : "x")),
        h("div", {}, h("strong", {}, r.correct ? praise() : "Not quite."), " ", r.explanation)));
      const nextBtn = h("button", { class: "btn btn-primary", onclick: () => show(i + 1) },
        i + 1 < qs.length ? "Next question" : "See result", icon("arrow"));
      const parts = [];
      if (!r.correct) {
        loadRewatch();
        const alt = alternatives[0];
        if (alt && !altOffered) {
          altOffered = true;
          parts.push(h("div", { class: "alt-offer" },
            h("div", {}, h("p", { class: "alt-label" }, "See it explained another way"),
              h("p", { class: "alt-concept" }, alt.snippet.concept),
              h("p", { class: "muted small" }, lectureLine(alt.lecture, alt.snippet))),
            h("button", { class: "btn btn-ghost", onclick: () => playAlt(alt) }, icon("play"), "Watch clip")));
        }
      }
      parts.push(nextBtn);
      next.replaceChildren(...parts);
      nextBtn.focus({ preventScroll: true });
    }
  };

  const finish = () => {
    const right = results.filter(Boolean).length;
    const all = right === qs.length;
    body.replaceChildren(h("div", { class: "quiz-done" },
      h("div", { class: `score ${all ? "is-all" : ""}` }, `${right}/${qs.length}`),
      h("div", {},
        h("p", { class: "quiz-q" }, all ? "You've got this one." : "Worth another look."),
        h("p", { class: "muted" }, all
          ? "Ask your next question whenever you're ready."
          : "We've added this clip to “Rewatch before the exam”.")),
      h("button", { class: "btn btn-ghost", onclick: () => { document.querySelector("#q").focus(); window.scrollTo({ top: 0, behavior: "smooth" }); } }, "Ask another")));
  };

  if (!qs.length) body.replaceChildren(h("p", { class: "muted" }, "No questions for this clip yet."));
  else show(0);
  return card;
}

const praise = () => ["Exactly.", "Spot on.", "That's it.", "Nice."][Math.floor(Math.random() * 4)];

// ------------------------------------------------------------------ rewatch panel

async function loadRewatch() {
  const list = $("#rewatch");
  let items = [];
  try { items = await api.rewatch(studentId()); } catch { /* keep empty state */ }
  $("#rewatch-count").textContent = items.length ? String(items.length) : "";
  if (!items.length) {
    list.replaceChildren(h("p", { class: "empty muted small" }, "Clips you miss a question on land here, so you know what to rewatch before the exam."));
    return;
  }
  list.replaceChildren(...items.map(({ snippet, lecture }) =>
    h("button", { class: "rw-item", onclick: () => openClip(snippet, lecture) },
      h("span", { class: "rw-play" }, icon("play")),
      h("span", { class: "rw-text" }, h("span", { class: "rw-concept" }, snippet.concept),
        h("span", { class: "muted small" }, lectureLine(lecture, snippet))))));
}

function openClip(snippet, lecture) {
  ++askToken;
  abortActiveChat?.(); abortActiveChat = null;
  document.body.classList.add("has-result");
  player?.destroy();
  const out = $("#result");
  out.replaceChildren(...foundView({ status: "found", snippet, lecture, homework: false, alternatives: [] }));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------------------------ start

loadRewatch();
if (!getName()) askName(false);

// AI status (round 4): read quietly. Without AI the student page looks exactly the same;
// the mode is only recorded on <body> for styling or debugging.
aiStatusP.then((s) => {
  document.body.dataset.aiMode = s.mode || "none";
  document.body.dataset.coverageCheck = s.coverage_check ? "1" : "0";
}).catch(() => {});
