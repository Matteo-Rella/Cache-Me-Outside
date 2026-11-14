// AI settings page (lecturer, local only): choose how Rewind gets its AI,
// test an API connection, or use a context package with any AI chat or agent.
import { api, downloadPackage } from "./api.js";
import { h, header, icon, lectureName, toast } from "./ui.js";

const $ = (s) => document.querySelector(s);
document.body.prepend(header("settings"));

const PROVIDERS = {
  anthropic: {
    label: "Anthropic (Claude)", keyHint: "sk-ant-…", keyUrl: "https://console.anthropic.com/settings/keys",
    models: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
  },
  openai: {
    label: "OpenAI", keyHint: "sk-…", keyUrl: "https://platform.openai.com/api-keys",
    models: ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
  },
  gemini: {
    label: "Google Gemini", keyHint: "AIza…", keyUrl: "https://aistudio.google.com/app/apikey",
    models: ["gemini-2.5-flash", "gemini-2.5-pro"],
  },
  openrouter: {
    label: "OpenRouter", keyHint: "sk-or-…", keyUrl: "https://openrouter.ai/settings/keys",
    models: ["anthropic/claude-haiku-5.5", "anthropic/claude-sonnet-5.5", "openai/gpt-4.1-mini", "google/gemini-2.5-flash"],
    // A preset of the OpenAI-compatible provider with a fixed base URL.
    preset: { provider: "openai_compatible", base_url: "https://openrouter.ai/api/v1" },
  },
  openai_compatible: {
    label: "OpenAI-compatible (Ollama, LM Studio, any server)", keyHint: "optional for local servers",
    models: ["llama3.1:8b", "qwen2.5:14b", "mistral-nemo", "anthropic/claude-sonnet-4.5"],
  },
};
const BASE_URL_HINTS = [
  ["Ollama", "http://localhost:11434/v1", "Runs on this laptop. Key: anything, e.g. “ollama”."],
  ["LM Studio", "http://localhost:1234/v1", "Start the local server in LM Studio first."],
];

/** Which dropdown entry shows the saved settings ("openrouter" is a preset of openai_compatible). */
const choiceOf = (s) => (s.preset === "openrouter" || (s.provider === "openai_compatible" && /openrouter\.ai/.test(s.base_url || ""))
  ? "openrouter" : s.provider);

const MODES = [
  {
    id: "api", icon: "bolt", title: "Use an API",
    text: "Rewind talks to an AI provider directly: Claude, OpenAI, Gemini, or a model running on this laptop.",
    gets: ["Make snippets in one click", "Smarter “not covered” check"],
  },
  {
    id: "package", icon: "package", title: "Use any AI chat or agent",
    tag: "No key needed",
    text: "Download a package, paste it into ChatGPT, Gemini or Claude (or give it to a coding agent), then bring the answer back.",
    gets: ["Works with any AI you already use", "Rewind checks the answer for you"],
  },
  {
    id: "none", icon: "off", title: "No AI",
    text: "Everything students use keeps working with the moments you already have.",
    gets: ["Search and quizzes still work", "No new snippets"],
  },
];

// Extra icons for this page (same stroke style as ui.js).
const EXTRA_ICONS = {
  bolt: '<path d="M13 3 5 14h6l-1 7 8-11h-6l1-7Z"/>',
  package: '<path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
  off: '<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>',
  download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  copy: '<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  upload: '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="2.8"/>',
  lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
};
function ico(name) {
  if (!EXTRA_ICONS[name]) return icon(name);
  const span = icon("x");
  span.querySelector("svg").innerHTML = EXTRA_ICONS[name];
  return span;
}

let saved = null; // public settings from the server
let mode = null; // mode selected on the page (may be unsaved)
let lectures = [];

// ------------------------------------------------------------------ load

async function load() {
  try {
    saved = await api.settings();
  } catch (err) {
    return locked(err);
  }
  mode = saved.mode || "none";
  try { lectures = (await api.lectures()) || []; } catch { lectures = []; }
  renderCurrent();
  renderModes();
  renderPanel();
  renderChatPolicy();
  renderLectures();
}

// ------------------------------------------------------------------ student chat policy (round 5)

const POLICIES = [
  { id: "strict", title: "Only course materials", tag: "Recommended",
    text: "Questions outside your lectures and slides get “This isn't in your course materials” and are sent to you." },
  { id: "labelled", title: "Short general answer, clearly labelled",
    text: "The AI may answer briefly under a “Not from your course materials” label. The question is still sent to you." },
];

function renderChatPolicy() {
  const box = $("#chat-policy");
  const supported = saved.chat_policy === "strict" || saved.chat_policy === "labelled";
  const current = supported ? saved.chat_policy : "strict";
  const note = saved.mode === "api"
    ? "Students can ask follow-up questions under each clip. Answers cite the exact moment or slide."
    : "The follow-up chat needs a live AI, so students only see it in “Use an API” mode. You can set this now.";
  const status = h("span", { class: "muted small cp-status", "aria-live": "polite" },
    supported ? "" : "Server default (not persisted by this server yet)");
  box.replaceChildren(
    h("div", { class: "card-head" }, h("h2", { class: "card-title" }, "Student chat"), status),
    h("p", { class: "muted small cp-note" }, note),
    h("div", { class: "cp-options", role: "radiogroup", "aria-label": "When a question is outside the course materials" },
      ...POLICIES.map((p) => {
        const on = p.id === current;
        return h("button", {
          class: `cp-opt ${on ? "is-selected" : ""}`, type: "button", role: "radio", "aria-checked": on ? "true" : "false",
          onclick: async () => {
            if (supported && p.id === saved.chat_policy) return;
            status.classList.remove("is-err");
            status.textContent = "Saving…";
            try {
              const { mode: m, provider, model, base_url, preset } = saved;
              const next = await api.saveSettings({ mode: m, provider, model, base_url, preset, chat_policy: p.id });
              saved = next;
              if (next.chat_policy !== p.id) {
                renderChatPolicy();
                const st = box.querySelector(".cp-status");
                if (st) {
                  st.classList.add("is-err");
                  st.textContent = "This server doesn't support saving chat_policy yet.";
                }
                return;
              }
              renderChatPolicy();
              toast("Chat setting saved.");
            } catch (err) {
              status.classList.add("is-err");
              status.textContent = `Couldn't save: ${err.message}`;
            }
          },
        },
          h("span", { class: "cp-radio" }),
          h("span", { class: "cp-body" },
            h("span", { class: "cp-title" }, p.title, p.tag ? h("span", { class: "badge badge-accent" }, p.tag) : null),
            h("span", { class: "cp-text" }, p.text)));
      })));
}

function locked(err) {
  $("#body").hidden = true;
  const box = $("#locked");
  box.hidden = false;
  const local = err.status === 403;
  box.replaceChildren(h("div", { class: "card set-locked" },
    h("span", { class: "set-locked-icon" }, ico("lock")),
    h("div", {},
      h("h2", { class: "card-title" }, local ? "Open this page on the Rewind computer" : "AI settings aren't available yet"),
      h("p", { class: "muted" }, local
        ? "For safety, AI settings only work on the laptop that runs Rewind (http://localhost:8000/settings.html). Students on the Wi-Fi can't open them."
        : `This Rewind server doesn't answer the settings endpoints (${err.message}). Update api.py, or open the page with ?mock=1 to try it.`))));
}

function renderCurrent() {
  const m = MODES.find((x) => x.id === saved.mode);
  const detail = saved.mode === "api"
    ? [PROVIDERS[choiceOf(saved)]?.label.split(" (")[0] || saved.provider, saved.model].filter(Boolean).join(" · ")
    : "";
  $("#current").replaceChildren(h("span", { class: `set-pill set-pill-${saved.mode}` },
    h("span", { class: "set-pill-dot" }), h("span", {}, h("span", { class: "muted" }, "Now: "), m ? m.title : "Not set"),
    detail ? h("span", { class: "set-pill-detail" }, detail) : null));
}

// ------------------------------------------------------------------ mode cards

function renderModes() {
  $("#modes").replaceChildren(...MODES.map((m) => {
    const on = m.id === mode;
    return h("button", {
      class: `card set-mode ${on ? "is-selected" : ""}`, type: "button", role: "radio", "aria-checked": on ? "true" : "false",
      onclick: () => { mode = m.id; renderModes(); renderPanel(); renderLectures(); },
    },
      h("span", { class: "set-mode-top" },
        h("span", { class: "set-mode-icon" }, ico(m.icon)),
        m.tag ? h("span", { class: "badge badge-accent" }, m.tag) : null,
        saved.mode === m.id ? h("span", { class: "badge" }, "In use") : null,
        h("span", { class: "set-mode-check" }, icon("check"))),
      h("span", { class: "set-mode-title" }, m.title),
      h("span", { class: "set-mode-text" }, m.text),
      h("span", { class: "set-mode-gets" }, ...m.gets.map((g) => h("span", {}, icon("check"), g))));
  }));
}

// ------------------------------------------------------------------ panels

function renderPanel() {
  const panel = $("#panel");
  if (mode === "api") panel.replaceChildren(...apiPanel());
  else if (mode === "package") panel.replaceChildren(...packagePanel());
  else panel.replaceChildren(...nonePanel());
}

function saveRow(extra = []) {
  const unsaved = mode !== saved.mode;
  return h("div", { class: "set-actions" },
    unsaved ? h("span", { class: "set-unsaved" }, "Not saved yet") : null,
    ...extra);
}

function apiPanel() {
  const provider = h("select", { class: "input", id: "provider" },
    ...Object.entries(PROVIDERS).map(([id, p]) => h("option", { value: id, selected: id === choiceOf(saved) ? "" : null }, p.label)));
  const model = h("input", { class: "input", id: "model", list: "model-list", value: saved.model || "", placeholder: "Model name", spellcheck: "false", autocomplete: "off" });
  const modelList = h("datalist", { id: "model-list" });
  const suggestions = h("div", { class: "set-suggest" });
  const keyInput = h("input", {
    class: "input", id: "api_key", type: "password", autocomplete: "off", spellcheck: "false",
    placeholder: saved.api_key ? `${saved.api_key} saved · leave empty to keep it` : "Paste your API key",
  });
  const showKey = h("button", { class: "set-eye", type: "button", "aria-label": "Show key", onclick: () => {
    keyInput.type = keyInput.type === "password" ? "text" : "password";
  } }, ico("eye"));
  const keyLink = h("a", { class: "small", target: "_blank", rel: "noopener" }, "Where do I get a key?");
  const baseUrl = h("input", { class: "input", id: "base_url", value: saved.base_url || "", placeholder: "http://localhost:11434/v1", spellcheck: "false" });
  const baseHints = h("div", { class: "set-hints" }, ...BASE_URL_HINTS.map(([name, url, note]) =>
    h("button", { class: "chip", type: "button", title: note, onclick: () => { baseUrl.value = url; baseUrl.focus(); } },
      h("strong", {}, name), h("span", { class: "muted" }, url))));
  const baseField = h("div", { class: "set-field set-span" },
    h("label", { for: "base_url" }, "Base URL"), baseUrl, baseHints,
    h("p", { class: "muted small" }, "Any server that speaks the OpenAI API: a model on this laptop, OpenRouter, Groq or your university's own server."));
  const result = h("div", { class: "set-result set-span", "aria-live": "polite" });

  const sync = () => {
    const p = PROVIDERS[provider.value];
    modelList.replaceChildren(...p.models.map((m) => h("option", { value: m })));
    suggestions.replaceChildren(h("span", { class: "muted small" }, "Suggestions:"),
      ...p.models.map((m) => h("button", { class: "chip chip-sm", type: "button", onclick: () => { model.value = m; } }, m)));
    keyInput.placeholder = saved.api_key && provider.value === choiceOf(saved)
      ? `${saved.api_key} saved · leave empty to keep it` : `Paste your key (${p.keyHint})`;
    keyLink.hidden = !p.keyUrl;
    if (p.keyUrl) keyLink.href = p.keyUrl;
    baseField.hidden = provider.value !== "openai_compatible";
  };
  provider.addEventListener("change", () => {
    if (!PROVIDERS[provider.value].models.includes(model.value)) model.value = PROVIDERS[provider.value].models[0];
    result.replaceChildren();
    sync();
  });
  sync();

  const values = () => {
    const preset = PROVIDERS[provider.value].preset;
    const v = preset
      ? { mode: "api", provider: preset.provider, preset: provider.value, model: model.value.trim(), base_url: preset.base_url }
      : { mode: "api", provider: provider.value, model: model.value.trim(), base_url: provider.value === "openai_compatible" ? baseUrl.value.trim() : "" };
    if (saved.chat_policy) v.chat_policy = saved.chat_policy;
    if (keyInput.value.trim()) v.api_key = keyInput.value.trim();
    return v;
  };
  const save = async (quiet) => {
    if (!model.value.trim()) { model.focus(); throw new Error("Choose a model first."); }
    saved = await api.saveSettings(values());
    keyInput.value = "";
    mode = saved.mode;
    renderCurrent(); renderModes(); sync(); renderChatPolicy();
    if (!quiet) toast("AI settings saved.");
  };

  const saveBtn = h("button", { class: "btn btn-primary", type: "submit" }, "Save");
  const testBtn = h("button", { class: "btn btn-ghost", type: "button" }, "Test connection");
  testBtn.addEventListener("click", async () => {
    testBtn.disabled = saveBtn.disabled = true;
    result.replaceChildren(h("div", { class: "set-msg is-pending" }, h("span", { class: "set-spin" }), "Saving and sending a tiny test request…"));
    try {
      await save(true);
      const r = await api.testConnection();
      result.replaceChildren(h("div", { class: `set-msg ${r.ok ? "is-ok" : "is-err"}` },
        h("span", { class: "set-msg-icon" }, icon(r.ok ? "check" : "x")),
        h("div", {}, h("strong", {}, r.ok ? "Connection works." : "Connection failed."), " ", r.message,
          r.ok && r.latency_ms ? h("span", { class: "muted" }, ` (${(r.latency_ms / 1000).toFixed(1)} s)`) : null)));
      renderLectures();
    } catch (err) {
      result.replaceChildren(h("div", { class: "set-msg is-err" }, h("span", { class: "set-msg-icon" }, icon("x")), h("div", {}, err.message)));
    } finally { testBtn.disabled = saveBtn.disabled = false; }
  });

  const form = h("form", { class: "set-form" },
    h("div", { class: "set-field" }, h("label", { for: "provider" }, "Provider"), provider),
    h("div", { class: "set-field" }, h("label", { for: "model" }, "Model"), model, modelList, suggestions),
    h("div", { class: "set-field set-span" },
      h("label", { for: "api_key" }, "API key ", keyLink),
      h("div", { class: "set-key" }, keyInput, showKey),
      h("p", { class: "muted small" }, "Stored on this computer only. Rewind never shows the full key again.")),
    baseField,
    result,
    h("div", { class: "set-span" }, saveRow([testBtn, saveBtn])));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    saveBtn.disabled = true;
    try { await save(false); renderLectures(); }
    catch (err) { result.replaceChildren(h("div", { class: "set-msg is-err" }, h("span", { class: "set-msg-icon" }, icon("x")), h("div", {}, err.message))); }
    finally { saveBtn.disabled = false; }
  });
  return [h("h2", { class: "card-title" }, "Connect an AI provider"), form];
}

function modeSaveButton(label) {
  const btn = h("button", { class: "btn btn-primary" }, label);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      saved = await api.saveSettings({
        mode, provider: saved.provider, model: saved.model, base_url: saved.base_url,
        ...(saved.preset ? { preset: saved.preset } : {}),
        ...(saved.chat_policy ? { chat_policy: saved.chat_policy } : {}),
      });
      renderCurrent(); renderModes(); renderPanel(); renderChatPolicy(); renderLectures();
      toast("AI settings saved.");
    } catch (err) { toast(`Couldn't save: ${err.message}`); btn.disabled = false; }
  });
  return btn;
}

function packagePanel() {
  const steps = [
    ["download", "Download", "the package for a lecture, or copy its prompt."],
    ["package", "Paste", "it into ChatGPT, Gemini or Claude. A coding agent can use the package directly."],
    ["upload", "Bring back", "the AI's answer below. Rewind checks it and tells you exactly what to fix."],
  ];
  return [
    h("h2", { class: "card-title" }, "Use the AI you already have"),
    h("ol", { class: "set-steps" }, ...steps.map(([ic, b, t], i) =>
      h("li", {}, h("span", { class: "set-step-icon" }, ico(ic), h("i", {}, String(i + 1))), h("p", {}, h("strong", {}, b), " ", t)))),
    mode !== saved.mode ? saveRow([modeSaveButton("Use this mode")]) : null,
  ].filter(Boolean);
}

function nonePanel() {
  const row = (ok, text) => h("li", { class: ok ? "is-ok" : "is-no" }, h("span", { class: "set-yn" }, icon(ok ? "check" : "x")), text);
  return [
    h("h2", { class: "card-title" }, "Run without AI"),
    h("ul", { class: "set-works" },
      row(true, "Students ask questions and get the right moment (search runs on this computer)."),
      row(true, "Check-yourself questions, rewatch list and lecturer dashboard."),
      row(true, "Homework-style questions are still recognised (by keywords)."),
      row(false, "Making snippets for new lectures. Switch to an API or a chat package for that.")),
    mode !== saved.mode ? saveRow([modeSaveButton("Turn AI off")]) : null,
  ].filter(Boolean);
}

// ------------------------------------------------------------------ lectures

function renderLectures() {
  const wrap = $("#lectures-wrap");
  const show = (mode === "api" && saved.mode === "api") || mode === "package";
  wrap.hidden = !show;
  if (!show) return;
  $("#lectures-title").textContent = mode === "api" ? "Make snippets with your API" : "Make snippets with a chat package";
  $("#lectures-sub").textContent = `${lectures.length} lecture${lectures.length === 1 ? "" : "s"}`;
  if (!lectures.length) {
    $("#lectures").replaceChildren(h("p", { class: "muted" }, "No lectures yet. Add one with the ingest tool, then come back here."));
    return;
  }
  $("#lectures").replaceChildren(...lectures.map((l) => lectureCard(l)));
}

function lectureCard(l) {
  const count = h("span", { class: "badge" }, `${l.snippet_count || 0} moments`);
  const head = h("div", { class: "set-lec-head" },
    h("div", {}, h("p", { class: "set-lec-name" }, lectureName(l)), h("p", { class: "muted small" }, l.title)), count);
  const body = mode === "api" ? generateBlock(l, count) : packageBlock(l, count);
  return h("article", { class: "card set-lec" }, head, body);
}

function generateBlock(l, count) {
  const bar = h("div", { class: "set-progress", hidden: true }, h("i"));
  const msg = h("p", { class: "muted small set-gen-msg" },
    l.snippet_count ? `Replaces this lecture's ${l.snippet_count} moments.` : "This lecture has no moments yet.");
  const btn = h("button", { class: "btn btn-primary" }, ico("bolt"), "Generate snippets");
  const poll = async () => {
    let st;
    try { st = await api.generateStatus(l.lecture_id); } catch (err) { st = { state: "error", message: err.message }; }
    if (st.state === "running") {
      msg.textContent = st.message || "Working…";
      setTimeout(poll, 1200);
      return;
    }
    bar.hidden = true; btn.disabled = false;
    if (st.state === "done") {
      msg.replaceChildren(h("span", { class: "set-ok-text" }, icon("check"), st.message || "Done."), " ",
        h("a", { href: `index.html${location.search}` }, "Open student page"));
      try { lectures = await api.lectures(); const fresh = lectures.find((x) => x.lecture_id === l.lecture_id); if (fresh) count.textContent = `${fresh.snippet_count || 0} moments`; } catch {}
    } else if (st.state === "error") {
      msg.replaceChildren(h("span", { class: "set-err-text" }, icon("x"), st.message || "Something went wrong."));
    }
  };
  const confirmBox = h("div", { class: "set-confirm", hidden: true });
  const start = async () => {
    confirmBox.hidden = true;
    btn.hidden = false; btn.disabled = true; bar.hidden = false;
    msg.hidden = false; msg.textContent = "Starting…";
    try {
      const r = await api.generate(l.lecture_id);
      if (r && r.ok === false) throw new Error(r.error || "Couldn't start.");
      setTimeout(poll, 600);
    } catch (err) {
      bar.hidden = true; btn.disabled = false;
      msg.replaceChildren(h("span", { class: "set-err-text" }, icon("x"), err.message));
    }
  };
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    msg.textContent = "Estimating the cost…";
    let est = null;
    try { est = await api.generateEstimate(l.lecture_id); }
    catch (err) { if (err.status !== 404 && err.status !== 501 && !/no route/.test(err.message)) console.warn(err); }
    btn.disabled = false;
    const cost = est && Number.isFinite(est.est_cost_usd)
      ? (est.est_cost_usd < 0.01 ? "less than $0.01" : `about $${est.est_cost_usd.toFixed(2)}`) : null;
    const calls = est && est.calls ? `${est.calls} AI call${est.calls === 1 ? "" : "s"}` : null;
    const line = calls && cost ? `This makes ${calls}, ${cost}.` : calls ? `This makes ${calls}.` : "This sends the whole transcript to your AI provider.";
    const detail = est && est.input_tokens
      ? `About ${Math.round(est.input_tokens / 100) / 10}k tokens in, ${Math.round((est.output_tokens || 0) / 100) / 10}k out.` : "";
    const go = h("button", { class: "btn btn-primary btn-sm", onclick: start }, "Generate");
    const cancel = h("button", { class: "btn btn-ghost btn-sm", onclick: () => {
      confirmBox.hidden = true; btn.hidden = false; msg.hidden = false;
      msg.textContent = l.snippet_count ? `Replaces this lecture's ${l.snippet_count} moments.` : "This lecture has no moments yet.";
    } }, "Cancel");
    confirmBox.replaceChildren(
      h("div", { class: "set-confirm-text" }, h("strong", {}, line, " Generate?"),
        h("span", { class: "muted small" }, [detail, l.snippet_count ? `Replaces the ${l.snippet_count} current moments.` : ""].filter(Boolean).join(" "))),
      cancel, go);
    btn.hidden = true; msg.hidden = true; confirmBox.hidden = false;
    go.focus();
  });
  return h("div", { class: "set-gen" }, h("div", { class: "set-gen-row" }, btn, confirmBox, h("div", { class: "set-gen-status" }, bar, msg)));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {}
  const ta = h("textarea", { style: { position: "fixed", opacity: "0" } }, text);
  document.body.append(ta); ta.select();
  const ok = document.execCommand("copy"); ta.remove();
  return ok;
}

function packageBlock(l, count) {
  const parts = h("div", { class: "set-parts" }, h("span", { class: "muted small" }, "Loading prompt…"));
  api.prompt(l.lecture_id).then(({ parts: ps }) => {
    parts.replaceChildren(...ps.map((text, i) => {
      const b = h("button", { class: "btn btn-ghost btn-sm" }, ico("copy"), `Copy prompt · part ${i + 1} of ${ps.length}`);
      b.addEventListener("click", async () => {
        const ok = await copyText(text);
        b.classList.add("is-copied");
        b.lastChild.textContent = ok ? `Copied part ${i + 1}` : "Copy failed";
        setTimeout(() => { b.classList.remove("is-copied"); b.lastChild.textContent = `Copy prompt · part ${i + 1} of ${ps.length}`; }, 1800);
      });
      return b;
    }));
  }).catch((err) => parts.replaceChildren(h("span", { class: "muted small" }, `No prompt available (${err.message}).`)));

  const dl = h("button", { class: "btn btn-ghost btn-sm" }, ico("download"), "Download package");
  dl.addEventListener("click", () => downloadPackage(l.lecture_id).catch((e) => toast(`Download failed: ${e.message}`)));

  const box = h("textarea", { class: "input set-paste", rows: 6, spellcheck: "false",
    placeholder: "Paste the AI's whole answer here. Prose and ```json blocks are fine, and you can paste several parts one after another." });
  const file = h("input", { type: "file", accept: ".json,.txt,.md", hidden: true });
  const fileBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => file.click() }, ico("upload"), "Upload file");
  const fileName = h("span", { class: "muted small" });
  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    box.value = await f.text();
    fileName.textContent = f.name;
  });
  const result = h("div", { class: "set-import-result", "aria-live": "polite" });
  const importBtn = h("button", { class: "btn btn-primary btn-sm" }, "Import");
  importBtn.addEventListener("click", async () => {
    if (!box.value.trim()) { box.focus(); return; }
    importBtn.disabled = true;
    result.replaceChildren(h("div", { class: "set-msg is-pending" }, h("span", { class: "set-spin" }), "Checking the answer…"));
    try {
      const r = await api.importAnswer(l.lecture_id, box.value);
      if (r.ok) {
        result.replaceChildren(h("div", { class: "set-msg is-ok" }, h("span", { class: "set-msg-icon" }, icon("check")),
          h("div", {}, h("strong", {}, `${r.snippets_count} moments imported.`), " Students can ask about this lecture now. ",
            h("a", { href: `index.html${location.search}` }, "Open student page"))));
        count.textContent = `${r.snippets_count} moments`;
        box.value = ""; fileName.textContent = "";
      } else {
        const copy = h("button", { class: "btn btn-primary btn-sm" }, ico("copy"), "Copy fix message for your AI");
        copy.addEventListener("click", async () => {
          const ok = await copyText(r.fix_message || "");
          copy.lastChild.textContent = ok ? "Copied. Paste it into the same chat" : "Copy failed";
        });
        result.replaceChildren(h("div", { class: "set-msg is-err set-msg-col" },
          h("div", { class: "set-msg-line" }, h("span", { class: "set-msg-icon" }, icon("x")),
            h("strong", {}, `The answer has ${r.errors.length} problem${r.errors.length === 1 ? "" : "s"}. Nothing was changed.`)),
          h("ul", { class: "set-errors" }, ...r.errors.slice(0, 8).map((e) => h("li", {}, e)),
            r.errors.length > 8 ? h("li", { class: "muted" }, `…and ${r.errors.length - 8} more (all included in the fix message).`) : null),
          h("div", { class: "set-msg-line" }, copy, h("span", { class: "muted small" }, "Send it to the AI, then paste its new answer here."))));
      }
    } catch (err) {
      result.replaceChildren(h("div", { class: "set-msg is-err" }, h("span", { class: "set-msg-icon" }, icon("x")), h("div", {}, err.message)));
    } finally { importBtn.disabled = false; }
  });

  return h("div", { class: "set-pack" },
    h("div", { class: "set-pack-row" }, dl, parts),
    h("label", { class: "set-pack-label" }, "The AI's answer"),
    box,
    h("div", { class: "set-pack-row" }, fileBtn, file, fileName, h("span", { class: "set-grow" }), importBtn),
    result);
}

load();
