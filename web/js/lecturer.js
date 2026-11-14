// Lecturer page: metrics, where students get stuck, uncovered questions, knowledge map.
import { api } from "./api.js";
import { ClipPlayer } from "./player.js";
import { fmt, h, header, icon, lectureName, modal, pct, toast } from "./ui.js";

const $ = (s) => document.querySelector(s);
const UNCLEAR = 0.4;
let lectures = {};
let stats = { totals: {}, snippets: [] };
let network = null;

document.body.prepend(header("lecturer"));

/** 0 -> green, 0.6+ -> red. */
export function failColor(rate, alpha = 1) {
  const r = Math.min(1, Math.max(0, (rate || 0) / 0.6));
  return `hsla(${Math.round(145 - 145 * r)}, ${62 + 10 * r}%, ${44 + 4 * r}%, ${alpha})`;
}

async function load() {
  const [ls, st, gaps, graph] = await Promise.all([
    api.lectures().catch(() => []), api.stats(), api.gaps().catch(() => []), api.graph().catch(() => null),
  ]);
  lectures = Object.fromEntries(ls.map((l) => [l.lecture_id, l]));
  stats = st;
  renderMetrics(st.totals);
  renderStuck(st.snippets);
  renderGaps(gaps);
  if (graph) renderGraph(graph);
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

function renderStuck(rows) {
  const sorted = [...rows].sort((a, b) => b.times_asked - a.times_asked || b.fail_rate - a.fail_rate);
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
      h("span", {}, ""), h("span", {}, "Moment"), h("span", {}, "Asked"), h("span", {}, "Fail rate")),
    ...shown.map((r, i) => {
      const unclear = r.fail_rate > UNCLEAR;
      const lec = lectures[r.lecture_id];
      return h("button", { class: `stuck-row ${unclear ? "is-unclear" : ""}`, onclick: () => playClip(r) },
        h("span", { class: "rank" }, String(i + 1)),
        h("span", { class: "stuck-main" },
          h("span", { class: "stuck-concept" }, r.concept),
          h("span", { class: "muted small" },
            `${lec ? lectureName(lec) : r.lecture_title} · ${fmt(r.start)}–${fmt(r.end)}`),
          unclear ? h("span", { class: "flag" }, icon("flag"), "possibly unclear") : null),
        h("span", { class: "asked" },
          h("span", { class: "asked-num" }, String(r.times_asked)),
          h("span", { class: "asked-bar" }, h("i", { style: { width: `${(100 * r.times_asked) / maxAsked}%` } }))),
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
    h("p", { class: "eyebrow" }, `${lectureName(lec)} · ${fmt(row.start)}–${fmt(row.end)}`),
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

function renderGraph(rawGraph) {
  if (!window.vis) { setTimeout(() => renderGraph(rawGraph), 100); return; }
  const graph = tidyGraph(rawGraph, lectures);
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim();
  const ink = css.getPropertyValue("--ink").trim();
  const snippetNodes = graph.nodes.filter((n) => n.type === "snippet");
  const asked = snippetNodes.map((n) => n.times_asked || 0);
  // With many clips, only label the most-asked ones; the rest show their name on hover.
  const labelled = graph.filtered ? new Set(snippetNodes.map((n) => n.id)) : new Set([...snippetNodes].sort((a, b) => (b.times_asked || 0) - (a.times_asked || 0))
    .slice(0, snippetNodes.length > 14 ? 10 : snippetNodes.length).filter((n) => snippetNodes.length <= 14 || n.times_asked)
    .map((n) => n.id));
  const maxAsked = Math.max(1, ...asked);
  const font = { face: "Inter, sans-serif", color: ink, size: 17 };

  const nodes = graph.nodes.map((n) => {
    if (n.type === "course") return { id: n.id, label: n.label, shape: "box", margin: 12, color: { background: ink, border: ink }, font: { ...font, color: "#fff", size: 20 }, level: 0 };
    if (n.type === "lecture") return { id: n.id, label: n.label, title: n.fullTitle, shape: "box", margin: 10, borderWidth: 1.5, color: { background: "#fff", border: ink }, font: { ...font, size: 18 }, level: 1 };
    if (n.type === "concept") return { id: n.id, label: n.label, shape: "box", margin: 8, borderWidth: 0, shapeProperties: { borderRadius: 14 }, color: { background: "#EEF0FF", border: "#EEF0FF", highlight: { background: "#E0E3FF", border: accent } }, font: { ...font, color: accent, size: 15 }, level: 3 };
    const size = 14 + 22 * Math.sqrt((n.times_asked || 0) / maxAsked);
    const c = failColor(n.fail_rate);
    return {
      id: n.id, label: labelled.has(n.id) ? wrap(n.label) : undefined, shape: "dot", size, level: 2,
      color: { background: c, border: "#fff", highlight: { background: c, border: ink }, hover: { background: c, border: ink } },
      borderWidth: 3, font: { ...font, size: 17, vadjust: 2 },
      title: h("div", {}, h("strong", {}, n.label), h("br"), `${n.times_asked || 0} times asked · ${pct(n.fail_rate)} fail rate`,
        h("br"), h("span", { class: "muted" }, "Click to play")),
      snippet_id: n.snippet_id,
    };
  });
  const edges = graph.edges.map((e) => ({ from: e.from, to: e.to, color: { color: "#D5D8E3", highlight: accent }, width: 1.4, smooth: { type: "continuous" } }));

  network?.destroy();
  network = new vis.Network($("#graph"), { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(edges) }, {
    layout: { improvedLayout: true },
    physics: { solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -120, springLength: 110, springConstant: 0.06, avoidOverlap: 1 }, stabilization: { iterations: 250 } },
    interaction: { hover: true, tooltipDelay: 120, zoomView: false },
  });
  network.once("stabilizationIterationsDone", () => {
    network.setOptions({ physics: false });
    network.fit({ maxZoomLevel: 1.5 });
  });
  network.on("click", (e) => {
    const node = nodes.find((n) => n.id === e.nodes[0]);
    if (!node?.snippet_id) return;
    const row = stats.snippets.find((s) => s.snippet_id === node.snippet_id);
    if (row) playClip(row);
  });
  network.on("hoverNode", (e) => { $("#graph").style.cursor = nodes.find((n) => n.id === e.node)?.snippet_id ? "pointer" : "default"; });
  network.on("blurNode", () => { $("#graph").style.cursor = "default"; });
}

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
