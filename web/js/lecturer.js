// Lecturer page: metrics, where students get stuck, uncovered questions, knowledge map.
import { api } from "./api.js";
import { ClipPlayer } from "./player.js";
import { fmt, h, header, icon, lectureName, mins, modal, pct, toast } from "./ui.js";

const $ = (s) => document.querySelector(s);
const UNCLEAR = 0.4;
let lectures = {};
let stats = { totals: {}, snippets: [] };
let network = null;

document.body.prepend(header("lecturer"));

const cssVar = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

/** Palette colours: blue normally, red when the fail rate is above the "unclear" line. */
export function failColor(rate) {
  return (rate || 0) > UNCLEAR ? cssVar("--mark", "#d00b1c") : cssVar("--accent", "#2e3f93");
}

async function load() {
  const [ls, st, gaps] = await Promise.all([
    api.lectures().catch(() => []), api.stats(), api.gaps().catch(() => []),
  ]);
  lectures = Object.fromEntries(ls.map((l) => [l.lecture_id, l]));
  stats = st;
  renderMetrics(st.totals);
  renderStuck(st.snippets);
  renderGaps(gaps);
  renderGraph();
  renderSlides(ls);
}

async function renderSlides(ls) {
  const card = $("#slides-card");
  try { await api.settings(); }
  catch (err) { if (err.status === 403 || err.status === 404) { card.hidden = true; return; } }
  if (!ls.length) { card.hidden = true; return; }
  card.hidden = false;
  $("#slides-list").replaceChildren(...ls.map((l) => {
    const status = h("span", { class: "muted small up-status" }, "Checking…");
    const file = h("input", { type: "file", accept: ".pdf,.pptx,application/pdf", hidden: true });
    const btn = h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => file.click() }, icon("send"), "Upload slides");
    const setCount = (n) => {
      status.classList.remove("is-err");
      status.textContent = n ? `${n} slides` : "No slides yet";
      btn.lastChild.textContent = n ? "Replace slides" : "Upload slides";
    };
    api.slides(l.lecture_id).then((s) => setCount(Array.isArray(s) ? s.length : 0)).catch(() => setCount(0));
    file.addEventListener("change", async () => {
      const f = file.files[0];
      if (!f) return;
      btn.disabled = true;
      status.classList.remove("is-err");
      status.replaceChildren(h("span", { class: "up-spin" }), ` Reading ${f.name}…`);
      try {
        const r = await api.uploadSlides(l.lecture_id, f);
        setCount(r.count);
        if (r.aligned != null) status.append(` · matched to ${r.aligned} clip${r.aligned === 1 ? "" : "s"}`);
        toast(`${r.count} slides added to ${lectureName(l)}.`);
      } catch (err) {
        if (err.status === 403) { card.hidden = true; return; }
        status.classList.add("is-err");
        status.textContent = `Upload failed: ${err.message}`;
      } finally { btn.disabled = false; file.value = ""; }
    });
    return h("div", { class: "up-row" },
      h("div", { class: "up-main" }, h("p", { class: "up-name" }, lectureName(l)), h("p", { class: "muted small" }, l.title)),
      status, btn, file);
  }));
}

function renderMetrics(t) {
  const card = (label, value, sub, cls = "") =>
    h("div", { class: `card metric ${cls}` }, h("p", { class: "metric-label" }, label),
      h("p", { class: "metric-value" }, value), h("p", { class: "muted small" }, sub));
  const fail = t.avg_fail_rate || 0;
  $("#metrics").replaceChildren(
    card("Questions asked", String(t.asks ?? 0), "by your students"),
    card("Answered by a clip", `${Math.round(t.found_pct ?? 0)}%`, "matched to a moment in your lectures"),
    card("Not covered", `${Math.round(t.not_covered_pct ?? 0)}%`, "sent to you as open questions"),
    card("Average fail rate", pct(fail), "on check-yourself questions", fail > UNCLEAR ? "is-bad" : ""));
}

let sortBy = "asked"; // "asked" | "followups"

function renderStuck(rows) {
  const hasFollowups = rows.some((r) => r.followups != null);
  if (!hasFollowups) sortBy = "asked";
  const key = (r) => (sortBy === "followups" ? r.followups || 0 : r.times_asked);
  const sorted = [...rows].sort((a, b) => key(b) - key(a) || b.times_asked - a.times_asked || b.fail_rate - a.fail_rate);
  const sub = $("#stuck-sub");
  if (hasFollowups) {
    const seg = (id, label) => h("button", { class: `seg-btn ${sortBy === id ? "is-on" : ""}`, type: "button", "aria-pressed": sortBy === id ? "true" : "false",
      onclick: () => { sortBy = id; renderStuck(rows); } }, label);
    sub.replaceChildren(h("span", { class: "seg", role: "group", "aria-label": "Sort by" }, seg("asked", "Most asked"), seg("followups", "Most follow-ups")));
  }
  $("#stuck").classList.toggle("has-followups", hasFollowups);
  const maxFollow = Math.max(1, ...rows.map((r) => r.followups || 0));
  if (!sorted.length) {
    $("#stuck").replaceChildren(h("p", { class: "empty muted" }, "No questions yet. Use “Load demo data” below to see an example."));
    return;
  }
  const maxAsked = Math.max(1, ...sorted.map((r) => r.times_asked));
  const TOP = 8;
  const expanded = $("#stuck").dataset.expanded === "1";
  const shown = expanded ? sorted : sorted.slice(0, TOP);
  const more = sorted.length > TOP
    ? h("button", { class: "btn btn-ghost btn-sm stuck-more", onclick: () => { $("#stuck").dataset.expanded = expanded ? "" : "1"; renderStuck(rows); } },
        expanded ? "Show top 8" : `Show all ${sorted.length} moments`)
    : null;
  $("#stuck").replaceChildren(
    h("div", { class: "stuck-row stuck-headrow muted small" },
      h("span", {}, ""), h("span", {}, "Moment"), h("span", {}, "Asked"),
      hasFollowups ? h("span", {}, "Follow-ups") : null, h("span", {}, "Fail rate")),
    ...shown.map((r, i) => {
      const unclear = r.fail_rate > UNCLEAR;
      const lec = lectures[r.lecture_id];
      return h("button", { class: `stuck-row ${unclear ? "is-unclear" : ""}`, onclick: () => playClip(r) },
        h("span", { class: "rank" }, String(i + 1)),
        h("span", { class: "stuck-main" },
          h("span", { class: "stuck-concept" }, r.concept),
          h("span", { class: "muted small" },
            `${lec ? lectureName(lec) : r.lecture_title} · ${mins(r.start, r.end)}`),
          unclear ? h("span", { class: "flag" }, icon("flag"), "possibly unclear") : null),
        h("span", { class: "asked" },
          h("span", { class: "asked-num" }, String(r.times_asked)),
          h("span", { class: "asked-bar" }, h("i", { style: { width: `${(100 * r.times_asked) / maxAsked}%` } }))),
        hasFollowups ? h("span", { class: "follow", title: "Follow-up questions in the chat about this clip" },
          h("span", { class: "asked-num" }, String(r.followups || 0)),
          h("span", { class: "asked-bar follow-bar" }, h("i", { style: { width: `${(100 * (r.followups || 0)) / maxFollow}%` } }))) : null,
        h("span", { class: "fail" },
          h("span", { class: "fail-bar" }, h("i", { style: { width: pct(r.fail_rate), background: failColor(r.fail_rate) } }),
            h("b", { class: "fail-mark", title: "40%" })),
          h("span", { class: `fail-num ${unclear ? "is-bad" : ""}` }, r.attempts ? pct(r.fail_rate) : "–")));
    }), more || "");
}

function renderGaps(gaps) {
  $("#gap-count").textContent = gaps.length ? String(gaps.length) : "";
  if (!gaps.length) {
    $("#gaps").replaceChildren(h("div", { class: "empty-ok" }, icon("check"), h("p", {}, "Every question so far was answered by your lectures.")));
    return;
  }
  $("#gaps").replaceChildren(...gaps.map((g) => {
    const btn = h("button", { class: "btn btn-ghost btn-sm" }, icon("check"), "Resolve");
    const row = h("div", { class: "gap" },
      h("div", { class: "gap-main" },
        h("p", { class: "gap-q" }, `“${g.question}”`),
        h("p", { class: "muted small" }, `${g.count} student${g.count === 1 ? "" : "s"} · ${ago(g.last_asked)}`)),
      btn);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api.resolveGap(g.gap_id);
        row.classList.add("is-leaving");
        setTimeout(async () => renderGaps(await api.gaps()), 220);
      } catch (e) { btn.disabled = false; toast(`Couldn't resolve: ${e.message}`); }
    });
    return row;
  }));
}

function ago(iso) {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!isFinite(d)) return "";
  if (d < 90) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  const days = Math.round(d / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

function playClip(row) {
  const lec = lectures[row.lecture_id];
  if (!lec) { toast("Lecture video not found."); return; }
  const mount = h("div", { class: "player-mount" });
  let p;
  modal(h("div", {},
    h("p", { class: "eyebrow" }, `${lectureName(lec)} · ${mins(row.start, row.end)}`),
    h("h2", { class: "modal-title" }, row.concept),
    mount,
    h("div", { class: "modal-stats" },
      h("span", {}, h("strong", {}, String(row.times_asked)), " times asked"),
      h("span", {}, h("strong", { style: { color: failColor(row.fail_rate) } }, pct(row.fail_rate)), ` fail rate (${row.attempts} attempts)`))),
  { cls: "modal-video", onClose: () => p?.destroy() });
  p = new ClipPlayer(mount, { lecture: lec, start: row.start, end: row.end });
}

const wrap = (t, w = 22) => String(t).split(" ").reduce((lines, word) => {
  const last = lines[lines.length - 1];
  if (last && (last + " " + word).length <= w) lines[lines.length - 1] = last + " " + word; else lines.push(word);
  return lines;
}, []).join("\n");

/** Short lecture labels; drop concept nodes that only repeat their own snippet's name. */
export function tidyGraph(graph, lecturesById = {}) {
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const norm = (s) => String(s || "").trim().toLowerCase();
  const neighbours = {};
  for (const e of graph.edges) {
    (neighbours[e.from] ||= []).push(e.to);
    (neighbours[e.to] ||= []).push(e.from);
  }
  const drop = new Set(graph.nodes.filter((n) => n.type === "concept" &&
    (neighbours[n.id] || []).every((id) => byId[id]?.type === "snippet" && norm(byId[id].label) === norm(n.label))).map((n) => n.id));
  const nodes = graph.nodes.filter((n) => !drop.has(n.id)).map((n) => {
    if (n.type !== "lecture") return n;
    const lec = lecturesById[n.id.replace(/^lecture:/, "")];
    return lec ? { ...n, label: lectureName(lec).replace(" · ", "\n"), fullTitle: lec.title } : n;
  });
  const MANY = 20, KEEP = 16;
  const snippets = nodes.filter((n) => n.type === "snippet");
  if (snippets.length > MANY) {
    const keep = new Set(snippets.filter((n) => n.times_asked > 0)
      .sort((a, b) => b.times_asked - a.times_asked).slice(0, KEEP).map((n) => n.id));
    const parentOf = {};
    for (const e of graph.edges) if (byId[e.from]?.type === "lecture" && byId[e.to]?.type === "snippet") parentOf[e.to] = e.from;
    const total = {};
    for (const n of snippets) total[parentOf[n.id]] = (total[parentOf[n.id]] || 0) + 1;
    for (const n of snippets) if (!keep.has(n.id)) drop.add(n.id);
    // concepts left with no snippet go too
    const linked = new Set(graph.edges.filter((e) => keep.has(e.from) || keep.has(e.to)).flatMap((e) => [e.from, e.to]));
    for (const n of nodes) if (n.type === "concept" && !linked.has(n.id)) drop.add(n.id);
    const shown = nodes.filter((n) => !drop.has(n.id)).map((n) => n.type === "lecture" && total[n.id]
      ? { ...n, label: `${n.label}\n${total[n.id]} clips` } : n);
    return { nodes: shown, edges: graph.edges.filter((e) => !drop.has(e.from) && !drop.has(e.to)), filtered: true };
  }
  const edges = graph.edges.filter((e) => !drop.has(e.from) && !drop.has(e.to));
  return { nodes, edges };
}

/** One bar per lecture: every moment is a block (width = its length). Darker = asked more, red = often failed. */
function renderGraph() {
  const byLecture = {};
  for (const r of stats.snippets) (byLecture[r.lecture_id] ||= []).push(r);
  const maxAsked = Math.max(1, ...stats.snippets.map((r) => r.times_asked || 0));
  network = null;
  $("#graph").replaceChildren(...Object.entries(byLecture).map(([id, rows]) => {
    rows.sort((x, y) => x.start - y.start);
    const lec = lectures[id];
    const asked = rows.reduce((n, r) => n + (r.times_asked || 0), 0);
    return h("div", { class: "lmap-row" },
      h("div", { class: "lmap-head" },
        h("strong", {}, lec ? lectureName(lec) : rows[0].lecture_title || id),
        h("span", { class: "muted small" }, `${rows.length} moments · ${asked} question${asked === 1 ? "" : "s"}`)),
      h("div", { class: "lmap-bar" }, ...rows.map((r) => {
        const bad = r.attempts > 0 && r.fail_rate > UNCLEAR;
        const level = Math.ceil(4 * (r.times_asked || 0) / maxAsked); // 0..4
        return h("button", { class: `lmap-seg lv${level} ${bad ? "is-bad" : ""}`, type: "button",
          style: { flexGrow: String(Math.max(1, Math.round(r.end - r.start))) },
          title: `${r.concept} · ${mins(r.start, r.end)} · asked ${r.times_asked || 0}×${r.attempts ? ` · ${pct(r.fail_rate)} wrong` : ""}`,
          "aria-label": `${r.concept}, ${mins(r.start, r.end)}`, onclick: () => playClip(r) });
      })),
      h("div", { class: "lmap-foot muted small" }, h("span", {}, "min 0"), h("span", {}, `min ${Math.round(rows[rows.length - 1].end / 60)}`)));
  }));
}

// ---- Add a lecture recording: upload, then poll the job and show the steps.
const STEPS = [["upload", "Uploading the recording"], ["transcribe", "Writing down what was said"], ["moments", "Cutting it into moments and writing check questions"], ["done", "Ready"]];
function showUpload(job) {
  const at = STEPS.findIndex(([k]) => k === job.step);
  $("#up-progress").hidden = false;
  $("#up-progress").replaceChildren(
    h("p", { class: "up-title" }, job.title || ""),
    h("div", { class: "up-bar" }, h("i", { style: { width: `${job.state === "done" ? 100 : Math.round((100 * (at + (job.step === "transcribe" ? job.percent / 100 : 0))) / (STEPS.length - 1))}%` } })),
    ...STEPS.map(([k, label], i) => h("p", { class: `up-step ${i < at || job.state === "done" ? "is-done" : i === at ? "is-on" : ""}` },
      h("i", {}), k === "transcribe" && i === at ? `${label} · ${job.percent}%` : k === "done" && job.state === "done" ? `Ready · ${job.snippet_count ?? 0} moments` : label)),
    job.state === "error" ? h("p", { class: "up-error" }, `It stopped: ${job.message}`) : job.message ? h("p", { class: "muted small" }, job.message) : "");
}
$("#upload-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("#up-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file); form.append("title", $("#up-title").value.trim()); form.append("course", $("#up-course").value.trim());
  const btn = e.currentTarget.querySelector("button"); btn.disabled = true;
  try {
    let job = await api.addLecture(form);
    showUpload(job);
    while (job.state === "running") { await new Promise((r) => setTimeout(r, 1500)); job = await api.uploadStatus(job.job_id); showUpload(job); }
    if (job.state === "done") { toast("Lecture added."); e.currentTarget?.reset?.(); $("#upload-form").reset(); await load(); }
  } catch (err) { toast(`Couldn't add the lecture: ${err.message}`); }
  finally { btn.disabled = false; }
});

$("#seed").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true;
  b.textContent = "Loading…";
  try { await api.seed(); await load(); toast("Demo data loaded."); }
  catch (err) { toast(`Couldn't load demo data: ${err.message}`); }
  finally { b.disabled = false; b.textContent = "Load demo data"; }
});

load().catch((err) => {
  $("#metrics").replaceChildren(h("div", { class: "card notice" }, h("h3", {}, "Couldn't load stats"), h("p", { class: "muted" }, err.message)));
});
