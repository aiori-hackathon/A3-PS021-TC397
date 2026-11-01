// Dialog Bridge dashboard (docs/DASHBOARD.md). Plain JavaScript, no libraries, no build step.
// Data: the bridge's WebSocket for events; everything else polled once a second from
// the supervisor, the agents, the actions service and the bridge (CORS allows this origin).
"use strict";

const S = {
  cfg: null,
  events: [], seen: new Set(), lastSeq: 0, bootId: null, wsUp: false, restartedFrom: null,
  status: {}, world: null, supervisorUp: false,
  tasks: [], byId: {}, dialogs: [], agents: {}, metrics: {}, faults: [], actionsFaults: [],
  ledger: [], chat: [], a: null, b: null, b2: null,
  crashedAt: {}, chatCount: 0, captionAt: 0,
  selectedTask: "", mode: "fast",
  run: null, runSelect: null, testStatus: {}, results: [], chaos: null, chaosFinal: null,
  replay: { events: [], timer: null },
  dlgSeen: new Set(), dlgOpen: new Set(), evOpen: new Set(),
  err: { bridge: false, actions: false, agentA: false, supervisor: false },
};

const REJECTIONS = new Set([
  "invalid_transition_rejected", "stale_fence_blocked", "schema_rejected",
  "request_id_conflict", "duplicate_request", "duplicate_rejected",
]);
const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const TESTS = [
  ["Official (S1–S5)", [
    ["s1", "Lost reply, sender retries"], ["s2", "Two workers race"],
    ["s3", "Worker crashes before the side effect"], ["s4", "Worker crashes after the side effect"],
    ["s5", "Bridge hard-killed during a network cut"]]],
  ["Baseline", [["s0", "Happy path"]]],
  ["Extra", [
    ["x1", "Uncertain side-effect outcome"], ["x2", "Slow, not dead, worker"],
    ["x3", "Orchestrator crashes after creating tasks"], ["x4", "Retry limit"],
    ["x5", "Cancel before side effect"], ["x6", "Dependency fails"], ["x7", "Duplicate dispatch"]]],
  ["Interop", [["i3", "Cross-adapter correlation"]]],
  ["Rules", [["rules", "R1–R4: invalid transition, schema, stale fence, reused key"]]],
  ["Mutation", [["m1", "M1: protection off must show duplicates"]]],
];
const SELECTION_IDS = {
  official: ["s1", "s2", "s3", "s4", "s5"],
  extra: ["x1", "x2", "x3", "x4", "x5", "x6", "x7"],
  all: ["s0", "s1", "s2", "s3", "s4", "s5", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "i3", "rules"],
  c1: ["c1"],
};

// --- helpers ----------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const short = (id) => (id ? String(id).slice(0, 10) + "…" : "");
const port = (name) => S.cfg.ports[name];

// Plain-JS JSON highlighter. `marks` are dotted paths whose line gets the "lifecycle state" highlight.
function renderJson(value, marks = [], path = "", depth = 0) {
  const pad = "  ".repeat(depth), pad1 = "  ".repeat(depth + 1);
  if (value === null) return '<span class="jb">null</span>';
  if (typeof value === "string") return `<span class="js">"${esc(value)}"</span>`;
  if (typeof value === "number") return `<span class="jn">${value}</span>`;
  if (typeof value === "boolean") return `<span class="jb">${value}</span>`;
  const comma = '<span class="jp">,</span>\n';
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="jp">[]</span>';
    return '<span class="jp">[</span>\n' +
      value.map((v, i) => pad1 + renderJson(v, marks, path ? `${path}.${i}` : String(i), depth + 1)).join(comma) +
      `\n${pad}<span class="jp">]</span>`;
  }
  const keys = Object.keys(value);
  if (!keys.length) return '<span class="jp">{}</span>';
  return '<span class="jp">{</span>\n' + keys.map((k) => {
    const p = path ? `${path}.${k}` : k;
    const line = `<span class="jk">"${esc(k)}"</span><span class="jp">: </span>${renderJson(value[k], marks, p, depth + 1)}`;
    return pad1 + (marks.includes(p) ? `<span class="jl map" title="carries the lifecycle state in this form">${line}</span>` : line);
  }).join(comma) + `\n${pad}<span class="jp">}</span>`;
}

function url(p, path) {
  return `${location.protocol}//${location.hostname}:${p}${path}`;
}

async function api(p, path, opts = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeout || 3000);
  try {
    const r = await fetch(url(p, path), {
      method: opts.method || "GET",
      headers: opts.body !== undefined ? { "content-type": "application/json" } : {},
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: ctl.signal,
    });
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    return { ok: r.ok, status: r.status, data };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String(e) };
  } finally {
    clearTimeout(timer);
  }
}

let toastTimer = null;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4500);
}

function banner(text, ok = false) {
  const el = $("banner");
  if (!text) { el.classList.add("hidden"); return; }
  el.textContent = text;
  el.classList.toggle("ok", ok);
  el.classList.remove("hidden");
}

function secondsUntil(iso) {
  return iso ? (Date.parse(iso) - Date.now()) / 1000 : null;
}

function groupOf(type) {
  for (const [g, types] of Object.entries(S.cfg.event_groups)) if (types.includes(type)) return g;
  return "";
}

function hhmmss(iso) {
  return iso ? iso.slice(11, 23) : "";
}

// --- event stream (WebSocket) -----------------------------------------------------------

function addEvent(e) {
  if (S.seen.has(e.seq)) return;
  S.seen.add(e.seq);
  S.events.push(e);
  if (S.events.length > 4000) {
    for (const old of S.events.splice(0, 1000)) S.seen.delete(old.seq);
  }
  S.lastSeq = Math.max(S.lastSeq, e.seq);
  if (e.type === "test_step" && Date.now() - Date.parse(e.ts) < 20000) {  // not a caption replayed from history
    $("caption-text").textContent = e.data.text;
    $("caption").classList.remove("hidden");
    S.captionAt = Date.now();
  }
  if (e.type === "bridge_started" && S.restartedFrom) {
    banner(`Bridge restarted — recovered ${e.data.recovered_tasks} task(s); event numbers continue at ${e.seq}`, true);
    toast(`Bridge restarted: recovered ${e.data.recovered_tasks} task${e.data.recovered_tasks === 1 ? "" : "s"}`);
    S.restartedFrom = null;
    setTimeout(() => banner(""), 9000);
  }
}

function connectWS() {
  const ws = new WebSocket(url(port("bridge"), "/ws").replace(/^http/, "ws"));
  ws.onopen = () => { S.wsUp = true; };
  ws.onmessage = (msg) => {
    const m = JSON.parse(msg.data);
    if (m.type === "hello") {
      const newBoot = S.bootId && m.boot_id !== S.bootId;
      if (m.last_seq < S.lastSeq) {
        // A wiped world (Reset): its event numbers start again at 1.
        S.events = []; S.seen = new Set(); S.lastSeq = 0;
        banner("New world: the event log starts again", true);
        setTimeout(() => banner(""), 5000);
      } else if (newBoot) {
        S.restartedFrom = S.bootId;
        banner("Bridge restarted — catching up with the event log…", true);
        setTimeout(() => banner(""), 9000);
      } else {
        banner("");
      }
      S.bootId = m.boot_id;
      const from = S.lastSeq || Math.max(0, m.last_seq - 600);
      ws.send(JSON.stringify({ type: "since", seq: from }));
    } else if (m.type === "event") {
      addEvent(m.event);
      scheduleRender();
    }
  };
  ws.onclose = () => {
    if (S.wsUp) banner("Bridge restarting… reconnecting");
    S.wsUp = false;
    setTimeout(connectWS, 1000);
  };
}

// --- polling ------------------------------------------------------------------------------

async function poll() {
  const sup = port("supervisor");
  const br = port("bridge");
  const [st, world, tasks, dialogs, agents, metrics, faults, ledger, afaults, chat, a, b] =
    await Promise.all([
      api(sup, "/status"), api(sup, "/world"),
      api(br, "/tasks"), api(br, "/dialogs"), api(br, "/agents"), api(br, "/metrics"), api(br, "/faults"),
      api(port("actions"), "/ledger"), api(port("actions"), "/faults"),
      api(port("agent_a"), "/chat"), api(port("agent_a"), "/state"), api(port("agent_b"), "/state"),
    ]);
  S.supervisorUp = st.ok;
  S.err = { supervisor: !st.ok, bridge: !(tasks.ok && dialogs.ok), actions: !ledger.ok, agentA: !chat.ok };
  if (st.ok) {
    for (const [name, p] of Object.entries(st.data)) {
      if (p.status === "crashed" && !S.crashedAt[name]) S.crashedAt[name] = new Date();
      if (p.status !== "crashed") delete S.crashedAt[name];
    }
    S.status = st.data;
  }
  if (world.ok) S.world = world.data;
  if (tasks.ok) {
    S.tasks = tasks.data;
    S.byId = Object.fromEntries(tasks.data.map((t) => [t.task_id, t]));
  }
  if (dialogs.ok) S.dialogs = dialogs.data;
  if (agents.ok) S.agents = Object.fromEntries(agents.data.map((x) => [x.agent_id, x]));
  if (metrics.ok) S.metrics = metrics.data;
  if (faults.ok) S.faults = faults.data;
  if (afaults.ok) S.actionsFaults = afaults.data;
  if (ledger.ok) S.ledger = ledger.data;
  if (chat.ok) S.chat = chat.data;
  S.a = a.ok ? a.data : null;
  S.b = b.ok ? b.data : null;
  if (S.status.agent_b2 && S.status.agent_b2.status !== "stopped") {
    const b2 = await api(port("agent_b2"), "/state");
    S.b2 = b2.ok ? b2.data : null;
  } else {
    S.b2 = null;
  }
  if (S.run && S.run.state === "running") await pollRun();
  if (S.runSelect === "c1" && S.run && S.run.state === "running") await pollChaos();
  render();
}

// --- rendering: header --------------------------------------------------------------------

const SVC_TONE = { online: "ok", starting: "warn", CRASHED: "bad", stopped: "warn", "?": "warn" };

function svcRow(label, state, extra) {
  const tone = SVC_TONE[state] ?? "warn";
  return `<div class="svc ${tone}"><span class="dot ${tone}" aria-hidden="true"></span>` +
    `<span class="svc-name">${esc(label)}${extra ? `<span class="svc-note">${esc(extra)}</span>` : ""}</span>` +
    `<span class="svc-state">${esc(state)}</span></div>`;
}

function heartbeatAgo(agentId) {
  const a = S.agents[agentId];
  if (!a || !a.last_heartbeat) return "";
  const s = Math.max(0, (Date.now() - Date.parse(a.last_heartbeat)) / 1000);
  return `last heartbeat ${s.toFixed(1)} s ago`;
}

function procState(name) {
  if (!S.supervisorUp) return "?";
  const p = S.status[name];
  if (!p) return "?";
  return { running: "online", starting: "starting", crashed: "CRASHED", stopped: "stopped" }[p.status] || p.status;
}

const SVC_LABEL = { bridge: "Bridge", agent_a: "Agent A", agent_b: "Agent B", agent_b2: "Agent B2", actions: "Actions" };

function healthSummary(rows) {
  // rows: [{ name, state }]. One short phrase for the top-bar pill, worst problem first.
  if (!S.supervisorUp) return { text: "Supervisor unreachable", tone: "warn" };
  const crashed = rows.filter((r) => r.state === "CRASHED");
  if (crashed.length === 1) return { text: `${SVC_LABEL[crashed[0].name]} crashed`, tone: "bad" };
  if (crashed.length > 1) return { text: `${crashed.length} services crashed`, tone: "bad" };
  const bridge = rows.find((r) => r.name === "bridge");
  if (bridge && bridge.state !== "online") return { text: "Bridge reconnecting", tone: "warn" };
  const online = rows.filter((r) => r.state === "online").length;
  return { text: `${online}/${rows.length} online`, tone: online === rows.length ? "ok" : "warn" };
}

function renderHeader() {
  const bridgeProc = procState("bridge");
  const bridge = S.wsUp ? "online" : bridgeProc === "online" ? "starting" : bridgeProc;
  const rows = [{ name: "bridge", state: bridge }, { name: "agent_a", state: procState("agent_a") }, { name: "agent_b", state: procState("agent_b") }];
  if (S.status.agent_b2 && S.status.agent_b2.status !== "stopped") rows.push({ name: "agent_b2", state: procState("agent_b2") });
  rows.push({ name: "actions", state: procState("actions") });

  const html = rows.map((r) => svcRow(
    r.name === "bridge" ? `Bridge :${port("bridge")}` : SVC_LABEL[r.name], r.state,
    r.name === "bridge" || r.name === "actions" ? "" : heartbeatAgo(r.name))).join("") +
    (S.supervisorUp ? "" : svcRow("Supervisor", "?", "not reachable"));
  const chips = $("chips");
  if (chips.dataset.html !== html) { chips.innerHTML = html; chips.dataset.html = html; }

  const sum = healthSummary(rows);
  $("health-text").textContent = sum.text;
  $("health-dot").className = `dot ${sum.tone}`;

  const on = !S.world || S.world.protection;
  const badge = $("protection-badge");
  badge.textContent = on ? "Dedup protection: ON" : "Dedup protection: OFF (testing only)";
  badge.className = "badge " + (on ? "on" : "off");
  document.body.classList.toggle("protection-off", !on);
  $("protection-banner").classList.toggle("hidden", on);
  const pbtn = $("btn-protection");
  pbtn.textContent = on ? "Dedup protection: ON" : "Dedup protection: OFF (testing only)";
  pbtn.classList.toggle("off", !on);

  const testing = S.world && S.world.test_running;
  if (!testing && S.captionAt && Date.now() - S.captionAt > 20000) $("caption").classList.add("hidden");
}

// --- rendering: Live tab -----------------------------------------------------------------

function setHtmlKeepOpen(el, html) {
  // Replace innerHTML only when it changed, and keep <details data-key> that the user opened.
  if (el.dataset.html === html) return false;
  const open = new Set([...el.querySelectorAll("details[open][data-key]")].map((d) => d.dataset.key));
  el.innerHTML = html;
  el.dataset.html = html;
  for (const d of el.querySelectorAll("details[data-key]")) if (open.has(d.dataset.key)) d.open = true;
  return true;
}

function formatPlan(text) {
  // "1. Detected intent: payment · ₹40 · bakery · scripted mode: pay ₹40 to bakery" -> pill tags
  const out = [];
  for (const line of text.split("\n")) {
    const m = line.match(/^(\d+)\. Detected intent: (.*) · (\w+) mode: (.*)$/);
    if (m) {
      const tags = m[2].split(" · ").map((t, i) => `<span class="tag${i === 0 ? " intent" : ""}">${esc(t)}</span>`).join("");
      out.push(`<div class="plan-line"><span class="plan-n">${esc(m[1])}</span>${tags}` +
        `<span class="tag mode">${esc(m[3])} mode</span><span class="plan-desc">${esc(m[4])}</span></div>`);
    } else if (/^Detected \d+ task/.test(line)) {
      out.push(`<div class="plan-head">${esc(line)}</div>`);
    } else {
      out.push(`<div class="plan-note">${esc(line)}</div>`);
    }
  }
  return `<div class="plan">${out.join("")}</div>`;
}

function renderChat() {
  const el = $("chat");
  const html = S.chat.map((m) => {
    const cls = m.kind === "user" ? "user" : m.kind === "notice" ? "notice" : m.kind === "summary" ? "summary" : "agent";
    const who = { user: "You", notice: "Bridge notice", summary: "Summary" }[m.kind] || "Agent A";
    let actions = "";
    if (m.kind === "plan" && m.dialog_id) {
      const d = S.dialogs.find((x) => x.dialog_id === m.dialog_id);
      const waiting = (d ? d.task_ids : []).map((id) => S.byId[id]).filter(
        (t) => t && t.needs_confirmation && !t.confirmed && !TERMINAL.has(t.state));
      actions = waiting.map((t) =>
        `<button class="good" data-act="confirm" data-task="${esc(t.task_id)}">Confirm task ${t.index} (${esc(t.intent)})</button>` +
        `<button class="danger" data-act="cancel" data-task="${esc(t.task_id)}">Cancel</button>`).join("");
      if (actions) actions = `<div class="actions">${actions}</div>`;
    }
    const body = m.kind === "plan" ? formatPlan(m.text) : esc(m.text);
    return `<div class="msg ${cls}"><span class="who">${esc(who)}</span>${body}${actions}</div>`;
  }).join("");
  if (el.dataset.html !== html) {
    el.innerHTML = html || '<div class="empty"><b>No messages yet</b><span>Press “Send task”, or type a request below.</span></div>';
    el.dataset.html = html;
    if (S.chat.length !== S.chatCount) el.scrollTop = el.scrollHeight;
    S.chatCount = S.chat.length;
  }
}

function protocolMessages(state, style) {
  if (!state || !state.messages) return { html: '<p class="hint">No messages yet.</p>', count: 0 };
  const list = state.messages.slice(-8).reverse();
  const html = list.map((m) => {
    const req = m.request;
    const rid = style === "mcp" ? (req.params && req.params._meta && req.params._meta.request_id) : (req.message && req.message.messageId);
    const err = m.reply && m.reply.error;
    const label = `${m.op} ${rid || ""}${err ? " → " + (err.code || err.message) : ""}`;
    return `<details class="pmsg ${err ? "err" : ""}" data-key="${esc(m.ts + "|" + m.op)}"><summary>${esc(label)}</summary>` +
      `<pre>${renderJson({ request: req, reply: m.reply })}</pre></details>`;
  }).join("");
  return { html: html || '<p class="hint">No messages yet.</p>', count: list.length };
}

function agentStatus(name, state) {
  const p = S.status[name] || {};
  const word = procState(name);
  const lines = [];
  if (state) lines.push(`<span>boot ${esc(state.boot_id)}</span>`);
  if (word === "CRASHED") {
    const at = S.crashedAt[name];
    lines.push(`<span><b>exit code ${esc(p.exit_code)}</b>${at ? " at " + at.toLocaleTimeString() : ""}</span>`);
  }
  if (p.env && Object.keys(p.env).length) lines.push(`<span>armed: ${esc(JSON.stringify(p.env))}</span>`);
  if (state && state.current) {
    const c = state.current;
    const t = S.byId[c.task_id];
    lines.push(`<span>task ${esc(short(c.task_id))} · step ${c.index} <b>${esc(c.step)}</b></span>`);
    lines.push(`<span>fence ${c.fence}${t ? " · attempt " + t.attempt : ""}</span>`);
    if (t && t.lease_until) lines.push(`<span class="lease" data-lease="${esc(t.lease_until)}"></span>`);
  } else if (state && name !== "agent_a") {
    lines.push(`<span>${state.paused ? "paused" : "idle, polling for work"}</span>`);
  }
  if (name === "agent_a" && state) lines.push(`<span>event cursor ${state.cursor} · auto-confirm ${state.auto_confirm ? "on" : "off"}</span>`);
  if (!state && word !== "CRASHED") lines.push("<span>no answer yet</span>");
  return lines.join("");
}

function setAgentBadge(prefix, name) {
  const word = procState(name);
  const tone = SVC_TONE[word] ?? "warn";
  $(`${prefix}-dot`).className = `dot ${tone}`;
  const badge = $(`${prefix}-badge`);
  badge.textContent = word;
  badge.className = `svc-state ${tone}`;
}

function renderAgents() {
  $("panel-a").classList.toggle("crashed", procState("agent_a") === "CRASHED");
  setAgentBadge("a", "agent_a");
  $("a-status").innerHTML = agentStatus("agent_a", S.a);
  const a = protocolMessages(S.a, "mcp");
  setHtmlKeepOpen($("a-msgs"), a.html);
  $("a-count").textContent = a.count || "";

  const bName = S.b2 && S.b2.current && !(S.b && S.b.current) ? "agent_b2" : "agent_b";
  const bState = bName === "agent_b2" ? S.b2 : S.b;
  $("panel-b").classList.toggle("crashed", procState(bName) === "CRASHED");
  $("panel-b").querySelector("h2").innerHTML =
    `${bName === "agent_b2" ? "Agent B2" : "Agent B"} <small>worker · A2A-style</small>`;
  setAgentBadge("b", bName);
  $("b-status").innerHTML = agentStatus(bName, bState);
  const b = protocolMessages(bState, "a2a");
  setHtmlKeepOpen($("b-msgs"), b.html);
  $("b-count").textContent = b.count || "";
  renderLeases();
}

function renderLeases() {
  for (const el of document.querySelectorAll("[data-lease]")) {
    const s = secondsUntil(el.dataset.lease);
    el.textContent = s > 0 ? `lease expires in ${s.toFixed(1)} s` : "lease EXPIRED";
    el.classList.add("lease");
    el.classList.toggle("low", s < 1.5);
  }
}

function setTile(id, value, label) {
  const tile = $(id);
  const n = tile.querySelector(".n");
  const text = String(value);
  if (n.textContent !== text) {
    const seen = n.dataset.seen;
    n.textContent = text;
    if (seen) { tile.classList.remove("bump"); void tile.offsetWidth; tile.classList.add("bump"); }
  }
  n.dataset.seen = "1";
  if (label !== undefined) tile.querySelector(".l").textContent = label;
}

function renderLedger() {
  const perKey = {};
  for (const r of S.ledger) perKey[r.idempotency_key] = (perKey[r.idempotency_key] || 0) + 1;
  const count = (a) => S.ledger.filter((r) => r.action === a).length;
  const dupKeys = Object.values(perKey).filter((n) => n > 1).length;
  const dupPayments = S.ledger.filter((r) => r.action === "pay" && perKey[r.idempotency_key] > 1).length;
  setTile("t-pay", count("pay"));
  $("t-pay").classList.toggle("bad", dupPayments > 0);
  setTile("t-send", count("send"));
  setTile("t-book", count("reserve"));
  setTile("t-rej", S.metrics.duplicate_effects_rejected ?? 0);
  $("t-dupfx").classList.toggle("hidden", !dupKeys);
  setTile("t-dupfx", dupKeys,
    `DUPLICATE side effect${dupKeys > 1 ? "s" : ""}: same key performed twice${dupPayments ? ` (${dupPayments} payment rows)` : ""}`);

  const last = S.ledger.slice(-6).reverse();
  setHtmlKeepOpen($("ledger-rows"), last.map((r) => {
    const dup = perKey[r.idempotency_key] > 1;
    return `<div class="lrow ${dup ? "dup" : ""}">${dup ? "DUPLICATE " : ""}${esc(r.action)} ${esc(r.receipt_id)}<br>key ${esc(r.idempotency_key)}</div>`;
  }).join("") || '<p class="hint">No side effects yet.</p>');
  $("rcpt-count").textContent = S.ledger.length || "";
}

function rulesFor(link) {
  return [...S.faults, ...S.actionsFaults].filter((r) => r.link === link);
}

function renderLinks() {
  const bridgeUp = S.wsUp;
  const hasB2 = S.status.agent_b2 && S.status.agent_b2.status !== "stopped";
  const linkState = (link, procs) => {
    const rules = rulesFor(link);
    const down = !bridgeUp || procs.some((p) => procState(p) !== "online");
    const cut = rules.some((r) => r.action === "cut");
    if (down) return { word: "DOWN", tone: "bad" };
    if (cut) return { word: "CUT", tone: "bad" };
    if (rules.length) return { word: "FAULTS ARMED", tone: "warn" };
    return { word: "connected", tone: "ok" };
  };
  const links = [
    { label: "Agent A ↔ Bridge", ...linkState("agent_a", ["agent_a"]) },
    { label: "Bridge ↔ Agent B", ...linkState("agent_b", ["agent_b"]) },
  ];
  if (hasB2) links.push({ label: "Bridge ↔ Agent B2", ...linkState("agent_b2", ["agent_b2"]) });
  links.push({ label: "Workers → Actions", ...linkState("actions", ["actions"]) });

  const worst = (list) => list.find((l) => l.tone === "bad") || list.find((l) => l.tone === "warn") || list[0];
  const wire = (l) => `<span class="wire ${l.tone}" title="${esc(l.label)}: ${esc(l.word)}"><i>${l.tone === "bad" ? "✕" : l.tone === "warn" ? "!" : ""}</i></span>`;
  const bLinks = links.filter((l) => l.label.includes("Agent B"));
  const diagram =
    `<div class="diagram" role="img" aria-label="${esc(links.map((l) => `${l.label} ${l.word}`).join(", "))}">` +
    `<span class="node">A</span>${wire(links[0])}<span class="node">Bridge</span>${wire(worst(bLinks))}` +
    `<span class="node">${hasB2 ? "B/B2" : "B"}</span>${wire(links[links.length - 1])}<span class="node">Actions</span></div>`;
  const problems = links.filter((l) => l.tone !== "ok");
  const notes = problems.length
    ? problems.map((l) => `<div class="link ${l.tone}"><span>${esc(l.label)}</span><span class="st">${esc(l.word)}</span></div>`).join("")
    : '<div class="link ok">All links connected</div>';
  setHtmlKeepOpen($("links"), diagram + notes);

  const all = [...S.faults, ...S.actionsFaults];
  $("rules").innerHTML = all.map((r) => `<div class="rule">${esc(r.id)} ${esc(r.link)} ${esc(r.action)}` +
    `${Object.keys(r.match).length ? " " + esc(JSON.stringify(r.match)) : ""} · ${r.count === -1 ? "until removed" : r.count + " left"}` +
    `${r.note ? " · " + esc(r.note) : ""}</div>`).join("");

  const cut = rulesFor("agent_a").find((r) => r.action === "cut");
  const cutBtn = $("btn-cut");
  cutBtn.textContent = cut ? "Reconnect A ↔ Bridge" : "Disconnect A ↔ Bridge";
  cutBtn.classList.toggle("active-toggle", !!cut);
  const slow = rulesFor("agent_b").find((r) => r.action === "delay" && r.count === -1);
  $("btn-slow-b").textContent = slow ? "Stop slowing Agent B" : "Slow Agent B";
  const crashed = Object.entries(S.status).filter(([, p]) => p.status === "crashed").map(([n]) => n);
  const recover = $("btn-recover");
  recover.disabled = !crashed.length;
  recover.textContent = crashed.length ? `Recover ${crashed.join(", ")}` : "Recover agent";
}

function stepProgress(t) {
  const n = t.steps.length;
  const done = t.state === "COMPLETED" ? n : t.last_checkpoint ? t.last_checkpoint.step_index + 1 : 0;
  return `step ${done}/${n}`;
}

function taskMeta(t) {
  if (t.state === "CREATED" && t.depends_on.length) return `waiting on task ${t.depends_on.join(", ")}`;
  if (t.state === "AWAITING_CONFIRMATION") return "waiting for confirmation";
  if (t.state === "DISPATCHED") return "ready; any worker may claim it";
  const bits = [stepProgress(t)];
  if (t.owner) bits.push(`owner ${t.owner}`);
  if (t.fence) bits.push(`fence ${t.fence}`);
  if (t.attempt) bits.push(`attempt ${t.attempt}`);
  if (t.effect.status !== "none") bits.push(`effect ${t.effect.status}${t.effect.replayed ? " (replayed)" : ""}`);
  if (t.reason) bits.push(t.reason);
  let lease = "";
  if ((t.state === "IN_PROGRESS" || t.state === "EFFECT_PENDING") && t.lease_until) {
    lease = ` · <span data-lease="${esc(t.lease_until)}"></span>`;
  }
  return esc(bits.join(" · ")) + lease;
}

const STATE_TONE = { COMPLETED: "ok", FAILED: "bad", CANCELLED: "bad", SUSPENDED: "warn", EFFECT_PENDING: "warn" };

function stepper(t) {
  // CREATED -> [AWAITING_CONFIRMATION] -> DISPATCHED -> IN_PROGRESS -> [EFFECT_PENDING] -> COMPLETED
  const steps = ["CREATED"];
  if (t.needs_confirmation) steps.push("AWAITING_CONFIRMATION");
  steps.push("DISPATCHED", "IN_PROGRESS");
  if (t.action) steps.push("EFFECT_PENDING");
  const failed = t.state === "FAILED" || t.state === "CANCELLED";
  steps.push(failed ? t.state : "COMPLETED");
  let now = steps.indexOf(t.state);
  let reached = now;
  if (t.state === "SUSPENDED") {
    now = steps.indexOf("IN_PROGRESS") + 1;
    steps.splice(now, 0, "SUSPENDED");
    reached = now;
  } else if (failed) {
    now = steps.length - 1;
    reached = t.attempt > 0 ? steps.indexOf("IN_PROGRESS") : 0; // how far it got before it ended
  }
  return `<ol class="stepper" aria-label="lifecycle of task ${t.index}: ${esc(t.state)}">` + steps.map((name, i) => {
    const cls = i === now ? `now ${STATE_TONE[t.state] || ""}` : i <= reached ? "done" : "";
    return `<li class="${cls}" title="${esc(name)}"></li>`;
  }).join("") + "</ol>";
}

function renderDialogs() {
  const list = S.dialogs.slice(-8).reverse();
  list.forEach((d, i) => {
    if (!S.dlgSeen.has(d.dialog_id)) {
      S.dlgSeen.add(d.dialog_id);
      if (i === 0) S.dlgOpen.add(d.dialog_id); // the newest dialog starts expanded, older ones collapsed
    }
  });
  const head = `<div class="tgrid thead"><span>Task</span><span>Intent</span><span>Lifecycle</span><span>State</span><span>Details</span></div>`;
  const html = list.map((d) => {
    const tasks = d.task_ids.map((id) => S.byId[id]).filter(Boolean);
    const rows = tasks.map((t) =>
      `<div class="tgrid trow" data-task="${esc(t.task_id)}" tabindex="0" role="button" title="${esc(t.task_id)}">` +
      `<span>task ${t.index}</span><span>${esc(t.intent)}</span>${stepper(t)}` +
      `<span><span class="state ${esc(t.state)}">${esc(t.state)}</span></span>` +
      `<span class="tmeta">${taskMeta(t)}</span></div>`).join("");
    const done = tasks.filter((t) => t.state === "COMPLETED").length;
    return `<details class="dlg" data-key="${esc(d.dialog_id)}"${S.dlgOpen.has(d.dialog_id) ? " open" : ""}>` +
      `<summary><span class="dlg-text">“${esc(d.text)}”</span>` +
      `<span class="dlg-meta">${esc(d.msg_id)} · ${esc(short(d.dialog_id))} · ${done}/${tasks.length} done</span></summary>` +
      `${head}${rows}</details>`;
  }).join("");
  const el = $("dialogs");
  const content = html || '<div class="empty"><b>No dialogs yet</b><span>Send a task to start one.</span></div>';
  if (el.dataset.html !== content) { el.innerHTML = content; el.dataset.html = content; }
  renderLeases();
}

// --- rendering: Tests tab ------------------------------------------------------------------

function statusOf(id) {
  const running = S.run && S.run.state === "running";
  // R1-R4 and M1 are several tests each; they only count as running when this run includes them.
  if (id === "rules") return worst(["r1", "r2", "r3", "r4"].map((r) => S.testStatus[r]), running && ["all", "rules"].includes(S.runSelect));
  if (id === "m1") return worst(["m1_s1", "m1_s4"].map((r) => S.testStatus[r]), running && S.runSelect === "m1");
  const st = S.testStatus[id];
  if (st) return st;
  if (running && (SELECTION_IDS[S.runSelect] || [S.runSelect]).includes(id)) return "RUNNING";
  return "NOT RUN";
}

function worst(list, running) {
  const known = list.filter(Boolean);
  for (const s of ["TIMEOUT", "FAIL", "ERROR"]) if (known.includes(s)) return s;
  if (known.length === list.length) return "PASS";
  return running ? "RUNNING" : "NOT RUN";
}

function evidenceFor(id) {
  const folder = id === "m1" ? "m1_s4" : id;
  for (const run of S.results) if (run.tests.includes(folder)) return { run: run.run_id, test: folder };
  return null;
}

const BAD_STATUS = new Set(["FAIL", "ERROR", "TIMEOUT"]);
const SUMMARY_GROUPS = [
  ["Official", "Official (S1–S5)"], ["Extra", "Extra"], ["Interop", "Interop"], ["Rules", "Rules"],
];

function groupStats(items) {
  const sts = items.map(([id]) => statusOf(id));
  const count = (f) => sts.filter(f).length;
  return {
    total: sts.length, pass: count((x) => x === "PASS"), fail: count((x) => BAD_STATUS.has(x)),
    running: count((x) => x === "RUNNING"), notRun: count((x) => x === "NOT RUN"),
  };
}

function summaryTile(label, st) {
  const tone = st.fail ? "bad" : st.pass === st.total ? "ok" : "";
  const bits = [];
  if (st.fail) bits.push(`${st.fail} failing`);
  if (st.running) bits.push(`${st.running} running`);
  if (st.notRun) bits.push(`${st.notRun} not run`);
  return `<div class="tile ${tone}"><span class="n">${st.pass}/${st.total}</span>` +
    `<span class="l">${esc(label)} · passing</span><span class="sub">${esc(bits.join(" · ") || "all passing")}</span></div>`;
}

function testRow(id, name) {
  const st = statusOf(id);
  const ev = evidenceFor(id);
  const menu = ev
    ? `<details class="rowmenu"><summary aria-label="More actions for ${esc(id.toUpperCase())}">⋯</summary><div class="menu-pop">` +
      `<a href="${esc(url(port("supervisor"), `/results/${ev.run}/${ev.test}/summary.json`))}" target="_blank" rel="noopener">Evidence file</a>` +
      `<button data-replay="${esc(ev.run)}|${esc(ev.test)}">Replay in Inspect</button></div></details>`
    : "<span></span>";
  return `<div class="test-row"><span class="tid">${esc(id.toUpperCase())}</span><span class="tname">${esc(name)}</span>` +
    `<span class="status ${st.replace(" ", "_")}">${esc(st)}</span>` +
    `<button class="run" data-run="${esc(id)}" aria-label="Run ${esc(id.toUpperCase())}">Run</button>${menu}</div>`;
}

function renderTests() {
  const cards = TESTS.map(([group, items]) => {
    const st = groupStats(items);
    return `<section class="panel test-card"><header class="test-card-head"><h2>${esc(group)}</h2>` +
      `<span class="hint">${st.pass}/${st.total} passing</span></header>` +
      items.map(([id, name]) => testRow(id, name)).join("") + "</section>";
  }).join("");
  const table = $("tests-table");
  if (table.dataset.html !== cards) { table.innerHTML = cards; table.dataset.html = cards; }

  const tiles = SUMMARY_GROUPS.map(([label, group]) =>
    summaryTile(label, groupStats(TESTS.find(([g]) => g === group)[1]))).join("");
  const summary = $("test-summary");
  if (summary.dataset.html !== tiles) { summary.innerHTML = tiles; summary.dataset.html = tiles; }

  const run = S.run;
  $("run-state").textContent = run
    ? `${run.state === "running" ? "Running" : "Finished"} ${run.select} (${run.mode}${run.protection ? "" : ", protection OFF"})` +
      (run.state === "done" ? ` · exit ${run.exit_code}` : "")
    : "No tests run yet. Start with “Run official five”.";
  if (!S.supervisorUp) $("run-state").textContent = "The supervisor is not reachable, so tests cannot be started or refreshed.";
  if (run && run.output) {
    const out = $("run-output");
    out.textContent = run.output.join("\n");
    out.scrollTop = out.scrollHeight;
  } else if (!run) {
    $("run-output").textContent = "Output appears here while tests run.";
  }
  for (const b of document.querySelectorAll("[data-run], #btn-run-official, #btn-run-all, #btn-run-chaos")) {
    b.disabled = !!(run && run.state === "running") || !S.supervisorUp;
  }

  const m = S.metrics;
  const rows2 = [
    ["tasks completed / failed / cancelled", `${m.tasks_completed ?? 0} / ${m.tasks_failed ?? 0} / ${m.tasks_cancelled ?? 0}`],
    ["duplicate requests absorbed", m.duplicate_requests], ["duplicate effects rejected", m.duplicate_effects_rejected],
    ["stale writes blocked", m.stale_writes_blocked], ["invalid transitions rejected", m.invalid_transitions_rejected],
    ["schema rejections", m.schema_rejections], ["lease expiries", m.lease_expiries],
    ["recoveries (resumed)", m.recoveries], ["reclaims", m.reclaims], ["faults applied", m.faults_applied],
    ["avg task (no failures)", m.avg_task_ms == null ? "—" : `${m.avg_task_ms} ms`],
    ["avg recovery after lease expiry", m.avg_recovery_ms == null ? "—" : `${m.avg_recovery_ms} ms`],
  ];
  $("metrics").innerHTML = rows2.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v ?? 0)}</td></tr>`).join("");

  const c = S.chaosFinal || S.chaos;
  const tile = (n, l) => `<div class="tile"><span class="n">${esc(n)}</span><span class="l">${l}</span></div>`;
  $("chaos-counters").innerHTML = c
    ? tile(c.tasks, "tasks") + tile(c.faults, "faults applied") + tile(c.crashes, "crashes") + tile(c.dupRejected, "duplicates rejected") +
      `<div class="tile wide emph ${c.dupEffects ? "bad" : "good"}"><span class="n">${esc(c.dupEffects)}</span>` +
      `<span class="l">duplicate side effects (must stay 0)${c.dupEffects ? " — PROBLEM" : ""}</span></div>` +
      `<div class="tile wide ${c.stuck ? "bad" : ""}"><span class="n">${esc(c.stuck ?? "—")}</span><span class="l">stuck tasks</span></div>`
    : '<div class="empty wide"><b>No chaos run yet</b><span>50 messages, random faults on every link, random crashes and two workers, in the separate test world.</span></div>';
  $("chaos-note").textContent = S.chaosFinal ? `Final numbers from ${S.chaosFinal.source}.` : S.chaos ? "Live, from the test world." : "";
}

async function pollRun() {
  const r = await api(port("supervisor"), `/tests/${encodeURIComponent(S.run.run_id)}?tail=60`);
  if (!r.ok) return;
  S.run = r.data;
  Object.assign(S.testStatus, r.data.tests);
  if (r.data.state === "done") {
    await loadResults();
    if (S.runSelect === "c1") await loadChaosFinal();
  }
}

async function pollChaos() {
  const tp = S.cfg.test_ports;
  const [m, l, st] = await Promise.all([
    api(tp.bridge, "/metrics"), api(tp.actions, "/ledger"), api(tp.supervisor, "/status"),
  ]);
  if (!m.ok) return;
  const perKey = {};
  for (const row of l.data || []) perKey[row.idempotency_key] = (perKey[row.idempotency_key] || 0) + 1;
  S.chaos = {
    tasks: m.data.tasks_total, faults: m.data.faults_applied,
    crashes: st.ok ? Object.values(st.data).reduce((n, p) => n + (p.crashes || 0), 0) : "—",
    dupRejected: m.data.duplicate_effects_rejected,
    dupEffects: Object.values(perKey).filter((n) => n > 1).length, stuck: null,
  };
}

async function loadChaosFinal(runId = S.run.run_id) {
  const ev = await api(port("supervisor"), `/results/${encodeURIComponent(runId)}/c1/summary.json`);
  if (!ev.ok) return;
  const n = ev.data.numbers;
  S.chaosFinal = {
    tasks: n.tasks, faults: n.bridge_faults_applied, crashes: n.crashes,
    dupRejected: n.duplicate_effects_rejected, dupEffects: n.duplicate_side_effects, stuck: n.stuck_tasks,
    source: `results/${runId}/c1 (${ev.data.status})`,
  };
}

async function loadResults() {
  const r = await api(port("supervisor"), "/results");
  if (r.ok) S.results = r.data;
  renderReplayRuns();
}

async function runTests(select) {
  const mode = ["all", "rules", "c1"].includes(select) ? "fast" : S.mode;
  const protection = !S.world || S.world.protection;
  const r = await api(port("supervisor"), "/tests/run", { method: "POST", body: { mode, select, protection }, timeout: 10000 });
  if (!r.ok) { toast(`Could not start tests: ${r.data ? r.data.detail : r.error}`); return; }
  S.run = r.data;
  S.runSelect = select;
  if (select === "c1") { S.chaos = null; S.chaosFinal = null; }
  for (const id of SELECTION_IDS[select] || [select]) delete S.testStatus[id];
  if (select === "m1") { delete S.testStatus.m1_s1; delete S.testStatus.m1_s4; }
  if (select === "rules") for (const id of ["r1", "r2", "r3", "r4"]) delete S.testStatus[id];
  toast(`Started ${select} in ${mode} mode${protection ? "" : " with protection OFF"}`);
  render();
}

// --- rendering: Inspect tab ----------------------------------------------------------------

function renderInspectOptions() {
  const sel = $("inspect-task");
  const opts = S.tasks.slice().reverse().map((t) =>
    `<option value="${esc(t.task_id)}">task ${t.index} ${esc(t.intent)} · ${esc(t.state)} · ${esc(t.task_id)}</option>`).join("");
  if (sel.dataset.html !== opts) {
    sel.innerHTML = opts || "<option value=''>no tasks yet</option>";
    sel.dataset.html = opts;
  }
  if (S.selectedTask) sel.value = S.selectedTask;
  else if (S.tasks.length) S.selectedTask = sel.value;

  const fd = $("f-dialog"), ft = $("f-task");
  const dOpts = '<option value="">all dialogs</option>' + S.dialogs.slice().reverse().map((d) =>
    `<option value="${esc(d.dialog_id)}">${esc(d.msg_id)} · ${esc(d.dialog_id)}</option>`).join("");
  if (fd.dataset.html !== dOpts) { const v = fd.value; fd.innerHTML = dOpts; fd.dataset.html = dOpts; fd.value = v; }
  const tOpts = '<option value="">all tasks</option>' + S.tasks.slice().reverse().map((t) =>
    `<option value="${esc(t.task_id)}">task ${t.index} ${esc(t.intent)} · ${esc(t.task_id)}</option>`).join("");
  if (ft.dataset.html !== tOpts) { const v = ft.value; ft.innerHTML = tOpts; ft.dataset.html = tOpts; ft.value = v; }
}

const EMPTY_VIEW = '<span class="jp">No task selected yet. Send a task from the Live tab, then pick it above.</span>';

async function renderInspector() {
  const id = S.selectedTask;
  if (!id) {
    for (const v of ["view-mcp", "view-shared", "view-a2a"]) $(v).innerHTML = EMPTY_VIEW;
    return;
  }
  const r = await api(port("bridge"), `/tasks/${encodeURIComponent(id)}/views`);
  if (!r.ok) return;
  $("view-mcp").innerHTML = renderJson(r.data.mcp_style, ["structuredContent.state"]);
  $("view-shared").innerHTML = renderJson(r.data.shared, ["state"]);
  $("view-a2a").innerHTML = renderJson(r.data.a2a_style, ["status.state", "metadata.bridge_state"]);
  const st = r.data.shared.state;
  $("inspect-note").textContent = st === "SUSPENDED"
    ? "SUSPENDED has no A2A-style equivalent: status.state shows \"working\" and the exact state travels in metadata.bridge_state."
    : `Same task in all three forms. A2A-style status.state "${r.data.a2a_style.status.state}" maps from bridge state ${st}; MCP-style returns the exact state as data.`;
}

function filteredEvents() {
  const d = $("f-dialog").value, t = $("f-task").value, g = $("f-group").value, rej = $("f-reject").checked;
  return S.events.filter((e) => (!d || e.dialog_id === d) && (!t || e.task_id === t) &&
    (!g || groupOf(e.type) === g) && (!rej || REJECTIONS.has(e.type)));
}

function renderEventLog() {
  const all = filteredEvents();
  const list = all.slice(-300).reverse();
  const head = '<div class="ev-grid ev-head" role="row"><span>Seq</span><span>Time</span><span>Type</span><span>Actor</span><span>Task</span><span>Data</span></div>';
  const rows = list.map((e) => {
    const open = S.evOpen.has(e.seq);
    const full = open ? `<pre class="ev-full">${renderJson({ seq: e.seq, ts: e.ts, type: e.type, actor: e.actor, dialog_id: e.dialog_id, task_id: e.task_id, data: e.data })}</pre>` : "";
    return `<div class="ev ev-grid ${groupOf(e.type)}${open ? " open" : ""}" data-seq="${e.seq}" tabindex="0" role="row" aria-expanded="${open}">` +
      `<span>${e.seq}</span><span>${esc(hhmmss(e.ts))}</span><span>${esc(e.type)}</span><span>${esc(e.actor)}</span>` +
      `<span title="${esc(e.task_id || "")}">${esc(short(e.task_id))}</span>` +
      `<span class="c-data" title="${esc(JSON.stringify(e.data))}">${esc(JSON.stringify(e.data))}</span>${full}</div>`;
  }).join("");
  const html = head + (rows || '<div class="empty"><b>No events match</b><span>Clear a filter, or send a task to create some.</span></div>');
  const el = $("eventlog");
  if (el.dataset.html !== html) {
    const top = el.scrollTop;
    el.innerHTML = html;
    el.dataset.html = html;
    el.scrollTop = top;
  }
  $("ev-count").textContent = `${all.length} event${all.length === 1 ? "" : "s"}${all.length > 300 ? " (latest 300 shown)" : ""}`;
}

function lane(actor) {
  if (actor === "agent_a") return 0;
  if (actor === "agent_b" || actor === "agent_b2") return 2;
  return 1;
}

function flowRow(e) {
  const cells = ["", "", "", ""];
  const cls = ["", "", "", ""];
  const d = e.data || {};
  if (e.type === "fault_applied") {
    const l = d.link === "agent_a" ? 0 : d.link === "actions" ? 3 : 2;
    cells[l] = `✕ ${d.action} ${d.op || ""}`;
    cls[l] = "drop";
    cells[1] = `fault ${d.rule_id}`;
  } else if (e.type === "duplicate_request") {
    const l = lane(e.actor);
    cells[l] = `↺ repeat ${d.request_id}`;
    cls[l] = "rej";
    cells[1] = "stored reply returned";
    cls[1] = "rej";
  } else if (e.type === "effect_committed") {
    cells[2] = `${e.actor}: effect reported`;
    cells[3] = `receipt ${d.receipt_id}${d.replayed ? " (replayed)" : ""}`;
    cls[3] = "on";
  } else if (e.type === "duplicate_rejected") {
    cells[3] = `same key: original receipt, no new effect`;
    cls[3] = "rej";
  } else if (e.type === "stale_fence_blocked" || e.type === "invalid_transition_rejected") {
    cells[lane(e.actor)] = `${e.actor}: ${d.op || ""}`;
    cells[1] = `✕ ${e.type}`;
    cls[1] = "drop";
  } else {
    const l = lane(e.actor);
    cells[l] = `${e.type}${d.to ? " → " + d.to : ""}`;
    cls[l] = "on";
    if (l !== 1) cells[1] = `#${e.seq}`;
  }
  return `<div class="frow" title="#${e.seq} ${esc(e.type)} ${esc(e.actor)}">` +
    cells.map((c, i) => `<span class="fcell ${cls[i]}">${c ? `<span title="${esc(c)}">${esc(c)}</span>` : ""}</span>`).join("") + "</div>";
}

function renderFlow() {
  const el = $("flow");
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
  const html = filteredEvents().filter((e) => e.type !== "test_step").slice(-80).map(flowRow).join("") ||
    '<p class="hint">No messages yet.</p>';
  if (el.dataset.html === html) return;
  el.innerHTML = html;
  el.dataset.html = html;
  if (atBottom) el.scrollTop = el.scrollHeight; // follow new events unless the user scrolled up
}

function renderReplayRuns() {
  const sel = $("replay-run");
  const html = S.results.map((r) => `<option value="${esc(r.run_id)}">${esc(r.run_id)}</option>`).join("");
  if (sel.dataset.html !== html) { const v = sel.value; sel.innerHTML = html; sel.dataset.html = html; if (v) sel.value = v; }
  renderReplayTests();
}

function renderReplayTests() {
  const run = S.results.find((r) => r.run_id === $("replay-run").value);
  const sel = $("replay-test");
  const html = (run ? run.tests : []).map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  if (sel.dataset.html !== html) { const v = sel.value; sel.innerHTML = html; sel.dataset.html = html; if (v) sel.value = v; }
}

async function loadReplay(runId, test) {
  const r = await api(port("supervisor"), `/results/${encodeURIComponent(runId)}/${encodeURIComponent(test)}/events.json`, { timeout: 8000 });
  if (!r.ok) { toast(`No events.json for ${runId}/${test}`); return; }
  S.replay.events = r.data;
  const slider = $("replay-slider");
  slider.max = Math.max(0, r.data.length - 1);
  slider.value = 0;
  renderReplay();
}

function renderReplay() {
  const evs = S.replay.events;
  const k = Number($("replay-slider").value);
  if (!evs.length) { $("replay-pos").textContent = ""; return; }
  const e = evs[k];
  $("replay-pos").textContent = `event ${k + 1} of ${evs.length} · seq ${e.seq}`;
  $("replay-event").innerHTML = `<h3>${esc(e.type)} · ${esc(e.actor)} · ${esc(hhmmss(e.ts))}</h3><pre class="code">${renderJson(e)}</pre>`;
  const tasks = {};
  const effectKeys = new Set();
  let dupRejected = 0;
  for (const x of evs.slice(0, k + 1)) {
    if (x.task_id) tasks[x.task_id] = tasks[x.task_id] || { state: "CREATED", step: "", effect: "none", fence: 0 };
    const t = tasks[x.task_id];
    if (x.type === "state_changed") t.state = x.data.to;
    if (x.type === "checkpoint") t.step = `${x.data.step_index}:${x.data.step}`;
    if (x.type === "effect_intent") t.effect = "intent";
    if (x.type === "effect_committed") { t.effect = "committed"; effectKeys.add(x.data.effect_key); }
    if (x.type === "duplicate_rejected") dupRejected += 1;
    if (x.data && x.data.fence) t.fence = x.data.fence;
  }
  $("replay-tasks").innerHTML = `<h3>State at this point</h3><table class="kv">` +
    Object.entries(tasks).map(([id, t]) => `<tr><td>${esc(short(id))} <span class="state ${esc(t.state)}">${esc(t.state)}</span></td>` +
      `<td>step ${esc(t.step || "—")} · effect ${esc(t.effect)} · fence ${t.fence}</td></tr>`).join("") +
    `</table><p>Side effects recorded (distinct keys): <b>${effectKeys.size}</b> · duplicates rejected: <b>${dupRejected}</b></p>`;
}

// --- render scheduling -----------------------------------------------------------------------

let renderPending = false;
function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; render(); });
}

function activeTab() {
  return document.querySelector(".tab.active").dataset.tab;
}

function render() {
  if (!S.cfg) return;
  renderHeader();
  const tab = activeTab();
  if (tab === "live") {
    $("chat-err").classList.toggle("hidden", !S.err.agentA);
    $("ledger-err").classList.toggle("hidden", !S.err.actions);
    $("dialogs-err").classList.toggle("hidden", !S.err.bridge);
    renderChat(); renderAgents(); renderLedger(); renderLinks(); renderDialogs();
  } else if (tab === "tests") {
    renderTests();
  } else {
    renderInspectOptions(); renderEventLog(); renderFlow();
  }
}

// --- actions ----------------------------------------------------------------------------------

async function sup(path, body, timeout = 20000) {
  const r = await api(port("supervisor"), path, { method: "POST", body: body ?? {}, timeout });
  if (!r.ok) toast(`Supervisor ${path} failed: ${r.data ? JSON.stringify(r.data.detail || r.data) : r.error}`);
  return r;
}

async function sendText(text) {
  if (!text.trim()) return;
  const r = await api(port("agent_a"), "/message", { method: "POST", body: { text }, timeout: 30000 });
  if (!r.ok) toast(`Agent A did not answer (${r.status || r.error})`);
  poll();
}

function bind() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      for (const t of document.querySelectorAll(".tab")) {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", t === tab);
      }
      for (const p of document.querySelectorAll(".tab-panel")) p.classList.add("hidden");
      const panel = $(`tab-${tab.dataset.tab}`);
      panel.classList.remove("hidden");
      panel.classList.add("enter");
      panel.addEventListener("animationend", () => panel.classList.remove("enter"), { once: true });
      render();
      if (tab.dataset.tab === "inspect") renderInspector();
      if (tab.dataset.tab === "tests") loadResults();
    });
  }
  const health = $("health"), healthBtn = $("health-btn");
  const setHealth = (open) => { health.classList.toggle("open", open); healthBtn.setAttribute("aria-expanded", open); };
  healthBtn.addEventListener("click", () => setHealth(!health.classList.contains("open")));
  document.addEventListener("click", (ev) => { if (!health.contains(ev.target)) setHealth(false); });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") setHealth(false); });
  document.addEventListener("click", (ev) => {  // close any open row menu when clicking elsewhere
    for (const m of document.querySelectorAll(".rowmenu[open]")) if (!m.contains(ev.target)) m.open = false;
  });
  $("chat-form").addEventListener("submit", (ev) => { ev.preventDefault(); sendText($("chat-input").value); });
  $("btn-send").addEventListener("click", () => sendText($("chat-input").value));
  $("chat").addEventListener("click", async (ev) => {
    const b = ev.target.closest("button[data-act]");
    if (!b) return;
    const r = await api(port("agent_a"), `/${b.dataset.act}`, { method: "POST", body: { task_id: b.dataset.task } });
    if (!r.ok) toast(`${b.dataset.act} refused: ${r.data && r.data.error ? r.data.error.message : r.status}`);
    poll();
  });
  $("btn-crash-b").addEventListener("click", async () => {
    const how = $("crash-mode").value;
    if (how === "kill") { await sup("/kill/agent_b"); toast("Agent B hard-killed"); }
    else { await sup("/restart/agent_b", { env: { CRASH_AT: how } }); toast(`Armed: Agent B will crash at ${how}`); }
    poll();
  });
  $("btn-cut").addEventListener("click", async () => {
    const cut = rulesFor("agent_a").find((r) => r.action === "cut");
    if (cut) await api(port("bridge"), `/faults/${cut.id}`, { method: "DELETE" });
    else await api(port("bridge"), "/faults", { method: "POST", body: { link: "agent_a", action: "cut", count: -1, note: "Dashboard: disconnect A ↔ Bridge" } });
    poll();
  });
  $("btn-retry").addEventListener("click", async () => {
    const r = await api(port("agent_a"), "/retry", { method: "POST", body: {}, timeout: 30000 });
    toast(r.ok ? `Retried ${r.data.op} with the same key ${r.data.request_id}: the bridge returned its stored reply`
      : `Retry: ${r.data && r.data.error ? r.data.error : "Agent A not reachable"}`);
    poll();
  });
  $("btn-recover").addEventListener("click", async () => {
    for (const [name, p] of Object.entries(S.status)) if (p.status === "crashed") await sup(`/restart/${name}`);
    poll();
  });
  $("btn-restart-bridge").addEventListener("click", async () => { await sup("/restart/bridge"); poll(); });
  $("btn-crash-a").addEventListener("click", async () => { await sup("/kill/agent_a"); poll(); });
  let resetTimer = null;
  const resetBtn = $("btn-reset");
  const disarmReset = () => { clearTimeout(resetTimer); resetBtn.classList.remove("armed"); resetBtn.textContent = "Reset demo"; };
  resetBtn.addEventListener("click", async () => {
    if (!resetBtn.classList.contains("armed")) {  // first click only arms it
      resetBtn.classList.add("armed");
      resetBtn.textContent = "Click again to confirm";
      resetTimer = setTimeout(disarmReset, 4000);
      return;
    }
    disarmReset();
    const env = S.world && !S.world.protection ? { DISABLE_DEDUP: "1" } : {};
    toast("Resetting the demo world…");
    await sup("/reset", { keep_running: true, env }, 60000);
    poll();
  });
  $("btn-drop-a").addEventListener("click", async () => {
    await api(port("bridge"), "/faults", { method: "POST", body: { link: "agent_a", action: "drop_reply", note: "Dashboard: drop next reply to A" } });
    poll();
  });
  $("btn-slow-b").addEventListener("click", async () => {
    const slow = rulesFor("agent_b").find((r) => r.action === "delay" && r.count === -1);
    if (slow) await api(port("bridge"), `/faults/${slow.id}`, { method: "DELETE" });
    else await api(port("bridge"), "/faults", { method: "POST", body: { link: "agent_b", action: "delay", delay_ms: Math.round(3 * S.cfg.lease_seconds * 1000), count: -1, note: "Dashboard: slow Agent B" } });
    poll();
  });
  $("btn-lose-pay").addEventListener("click", async () => {
    await api(port("actions"), "/faults", { method: "POST", body: { link: "actions", action: "drop_reply", match: { action: "pay" }, note: "Dashboard: lose payment reply" } });
    poll();
  });
  $("btn-clear-faults").addEventListener("click", async () => {
    await Promise.all([api(port("bridge"), "/faults", { method: "DELETE" }), api(port("actions"), "/faults", { method: "DELETE" })]);
    poll();
  });
  const openTask = (row) => {
    if (!row || !row.dataset.task) return;
    S.selectedTask = row.dataset.task;
    document.querySelector('.tab[data-tab="inspect"]').click();
  };
  $("dialogs").addEventListener("click", (ev) => openTask(ev.target.closest(".trow")));
  $("dialogs").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" || ev.key === " ") { const row = ev.target.closest(".trow"); if (row) { ev.preventDefault(); openTask(row); } }
  });
  $("dialogs").addEventListener("toggle", (ev) => {  // toggle does not bubble: capture it
    const d = ev.target;
    if (!d.dataset || !d.dataset.key) return;
    if (d.open) S.dlgOpen.add(d.dataset.key); else S.dlgOpen.delete(d.dataset.key);
  }, true);
  $("ledger-counters").addEventListener("animationend", (ev) => ev.target.classList.remove("bump"));

  for (const b of document.querySelectorAll("[data-mode]")) {
    b.addEventListener("click", () => {
      S.mode = b.dataset.mode;
      try { localStorage.setItem("dashboard.mode", S.mode); } catch (e) { /* private window */ }
      for (const x of document.querySelectorAll("[data-mode]")) {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", x === b);
      }
    });
  }
  $("btn-protection").addEventListener("click", async () => {
    const on = !S.world || S.world.protection;
    toast(on ? "Turning protection OFF: resetting the demo world with DISABLE_DEDUP=1" : "Turning protection back ON: resetting the demo world");
    await sup("/reset", { keep_running: true, env: on ? { DISABLE_DEDUP: "1" } : {} }, 60000);
    poll();
  });
  $("btn-run-official").addEventListener("click", () => runTests("official"));
  $("btn-run-all").addEventListener("click", () => runTests("all"));
  $("btn-run-chaos").addEventListener("click", () => runTests("c1"));
  $("tests-table").addEventListener("click", (ev) => {
    const run = ev.target.closest("button[data-run]");
    if (run) runTests(run.dataset.run);
    const rep = ev.target.closest("button[data-replay]");
    if (rep) {
      for (const m of document.querySelectorAll(".rowmenu[open]")) m.open = false;
      const [runId, test] = rep.dataset.replay.split("|");
      document.querySelector('.tab[data-tab="inspect"]').click();
      $("replay-run").value = runId;
      renderReplayTests();
      $("replay-test").value = test;
      loadReplay(runId, test);
    }
  });

  for (const b of document.querySelectorAll(".view-tabs [data-view]")) {  // JSON views as tabs on narrow screens
    b.addEventListener("click", () => {
      for (const x of document.querySelectorAll(".view-tabs [data-view]")) {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-selected", x === b);
      }
      for (const col of document.querySelectorAll(".view-col")) col.classList.toggle("on", col.dataset.view === b.dataset.view);
    });
  }
  const toggleEvent = (row) => {
    if (!row) return;
    const seq = Number(row.dataset.seq);
    if (S.evOpen.has(seq)) S.evOpen.delete(seq); else S.evOpen.add(seq);
    renderEventLog();
  };
  $("eventlog").addEventListener("click", (ev) => { if (!ev.target.closest(".ev-full")) toggleEvent(ev.target.closest(".ev")); });
  $("eventlog").addEventListener("keydown", (ev) => {
    if ((ev.key === "Enter" || ev.key === " ") && !ev.target.closest(".ev-full")) { ev.preventDefault(); toggleEvent(ev.target.closest(".ev")); }
  });

  $("inspect-task").addEventListener("change", (ev) => { S.selectedTask = ev.target.value; renderInspector(); });
  for (const id of ["f-dialog", "f-task", "f-group", "f-reject"]) $(id).addEventListener("change", render);
  $("replay-run").addEventListener("change", renderReplayTests);
  $("btn-replay-load").addEventListener("click", () => loadReplay($("replay-run").value, $("replay-test").value));
  $("replay-slider").addEventListener("input", renderReplay);
  $("btn-replay-play").addEventListener("click", () => {
    const btn = $("btn-replay-play");
    if (S.replay.timer) { clearInterval(S.replay.timer); S.replay.timer = null; btn.textContent = "Play"; return; }
    btn.textContent = "Pause";
    S.replay.timer = setInterval(() => {
      const s = $("replay-slider");
      if (Number(s.value) >= Number(s.max)) { clearInterval(S.replay.timer); S.replay.timer = null; btn.textContent = "Play"; return; }
      s.value = Number(s.value) + 1;
      renderReplay();
    }, 400);
  });
}

async function restoreRuns() {
  // Statuses survive a page reload: the supervisor remembers the runs it started.
  const r = await api(port("supervisor"), "/tests");
  if (!r.ok || !r.data.length) return;
  for (const run of r.data.slice().reverse()) Object.assign(S.testStatus, run.tests);
  S.run = r.data[0];
  S.runSelect = S.run.select;
  const chaos = r.data.find((x) => x.select === "c1" && x.state === "done");
  if (chaos) await loadChaosFinal(chaos.run_id);  // the last chaos run's numbers survive a reload
}

function restoreMode() {
  let mode = "fast";
  try { mode = localStorage.getItem("dashboard.mode") || "fast"; } catch (e) { mode = "fast"; }
  const btn = document.querySelector(`[data-mode="${mode}"]`);
  if (btn) btn.click();
}

function trackStackHeight() {
  // The Live tab fills the window below the sticky top stack, whatever banners are showing.
  const stack = document.querySelector(".top-stack");
  const set = () => document.documentElement.style.setProperty("--stack-h", `${stack.offsetHeight}px`);
  set();
  if (window.ResizeObserver) new ResizeObserver(set).observe(stack);
}

function fatal(message) {
  // Nothing can work without the bridge's /config.json: say so plainly, with a retry.
  for (const m of document.querySelectorAll(".tab-panel")) m.classList.add("hidden");
  document.querySelector(".tabs").classList.add("hidden");  // nothing to switch to
  const box = document.createElement("main");
  box.className = "panel fatal";
  box.setAttribute("role", "alert");
  box.innerHTML = `<h2>Can't reach the Dialog Bridge</h2><p class="hint">${esc(message)}</p>` +
    `<p class="hint">Start it with <code>python supervisor.py --mode demo</code>, then try again.</p>` +
    `<button class="primary" id="btn-retry-load">Try again</button>`;
  document.body.appendChild(box);
  $("btn-retry-load").addEventListener("click", () => location.reload());
}

async function start() {
  trackStackHeight();
  try {
    const r = await fetch("/config.json");
    if (!r.ok) throw new Error(`the bridge answered HTTP ${r.status}`);
    S.cfg = await r.json();
  } catch (e) {
    fatal(`The dashboard could not load its configuration (${e.message || e}).`);
    return;
  }
  bind();
  restoreMode();
  connectWS();
  await restoreRuns();
  await poll();
  await loadResults();
  setInterval(poll, 1000);
  setInterval(renderLeases, 250);
  setInterval(() => { if (activeTab() === "inspect") renderInspector(); }, 2000);
}

start();
