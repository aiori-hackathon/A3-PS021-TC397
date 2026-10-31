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
const json = (v) => esc(JSON.stringify(v, null, 2));
const port = (name) => S.cfg.ports[name];

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
  if (e.type === "test_step") {
    $("caption-text").textContent = e.data.text;
    $("caption").classList.remove("hidden");
    S.captionAt = Date.now();
  }
  if (e.type === "bridge_started" && S.restartedFrom) {
    banner(`Bridge restarted — recovered ${e.data.recovered_tasks} task(s); event numbers continue at ${e.seq}`, true);
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

function chip(label, state, extra) {
  const cls = { online: "ok", running: "ok", starting: "warn", CRASHED: "bad", stopped: "", "?": "warn" }[state] ?? "";
  return `<span class="chip ${cls}">${esc(label)} <b>${esc(state)}</b>${extra ? " · " + esc(extra) : ""}</span>`;
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

function renderHeader() {
  const parts = [];
  const bridge = S.wsUp ? "online" : procState("bridge") === "online" ? "starting" : procState("bridge");
  parts.push(chip(`Bridge :${port("bridge")}`, bridge));
  parts.push(chip("Agent A", procState("agent_a"), heartbeatAgo("agent_a")));
  parts.push(chip("Agent B", procState("agent_b"), heartbeatAgo("agent_b")));
  if (S.status.agent_b2 && S.status.agent_b2.status !== "stopped") {
    parts.push(chip("Agent B2", procState("agent_b2"), heartbeatAgo("agent_b2")));
  }
  parts.push(chip("Actions", procState("actions")));
  if (!S.supervisorUp) parts.push(chip("Supervisor", "?", "not reachable"));
  $("chips").innerHTML = parts.join("");

  const on = !S.world || S.world.protection;
  const badge = $("protection-badge");
  badge.textContent = on ? "Dedup protection: ON" : "Dedup protection: OFF (testing only)";
  badge.className = "badge " + (on ? "on" : "off");
  document.body.classList.toggle("protection-off", !on);
  const pbtn = $("btn-protection");
  pbtn.textContent = on ? "Dedup protection: ON" : "Dedup protection: OFF (testing only)";
  pbtn.classList.toggle("off", !on);

  const testing = S.world && S.world.test_running;
  if (!testing && S.captionAt && Date.now() - S.captionAt > 20000) $("caption").classList.add("hidden");
}

// --- rendering: Live tab -----------------------------------------------------------------

function renderChat() {
  const el = $("chat");
  const html = S.chat.map((m) => {
    const cls = m.kind === "user" ? "user" : m.kind === "notice" ? "notice" : m.kind === "summary" ? "agent summary" : "agent";
    const who = { user: "You", notice: "Bridge notice", summary: "Agent A · summary" }[m.kind] || "Agent A";
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
    return `<div class="msg ${cls}"><span class="who">${esc(who)}</span>${esc(m.text)}${actions}</div>`;
  }).join("");
  if (el.dataset.html !== html) {
    el.innerHTML = html || '<p class="hint">Type a request below and press Send.</p>';
    el.dataset.html = html;
    if (S.chat.length !== S.chatCount) el.scrollTop = el.scrollHeight;
    S.chatCount = S.chat.length;
  }
}

function protocolMessages(state, style) {
  if (!state || !state.messages) return '<p class="hint">No messages yet.</p>';
  return state.messages.slice(-8).reverse().map((m) => {
    const req = m.request;
    const rid = style === "mcp" ? (req.params && req.params._meta && req.params._meta.request_id) : (req.message && req.message.messageId);
    const err = m.reply && m.reply.error;
    const label = `${m.op} ${rid || ""}${err ? " → " + (err.code || err.message) : ""}`;
    return `<details class="pmsg ${err ? "err" : ""}"><summary>${esc(label)}</summary>` +
      `<pre>${json({ request: req, reply: m.reply })}</pre></details>`;
  }).join("");
}

function agentStatus(name, state) {
  const p = S.status[name] || {};
  const word = procState(name);
  const lines = [`<span class="state-word"><b>${esc(word)}</b>${state ? " · boot " + esc(state.boot_id) : ""}</span>`];
  if (word === "CRASHED") {
    const at = S.crashedAt[name];
    lines.push(`<span>exit code ${esc(p.exit_code)}${at ? " at " + at.toLocaleTimeString() : ""}</span>`);
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
  return lines.join("");
}

function renderAgents() {
  $("panel-a").classList.toggle("crashed", procState("agent_a") === "CRASHED");
  $("a-status").innerHTML = agentStatus("agent_a", S.a);
  $("a-msgs").innerHTML = protocolMessages(S.a, "mcp");
  const bName = S.b2 && S.b2.current && !(S.b && S.b.current) ? "agent_b2" : "agent_b";
  const bState = bName === "agent_b2" ? S.b2 : S.b;
  $("panel-b").classList.toggle("crashed", procState(bName) === "CRASHED");
  $("panel-b").querySelector("h2").innerHTML =
    `${bName === "agent_b2" ? "Agent B2" : "Agent B"} <small>worker · A2A-style</small>`;
  $("b-status").innerHTML = agentStatus(bName, bState);
  $("b-msgs").innerHTML = protocolMessages(bState, "a2a");
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

function renderLedger() {
  const perKey = {};
  for (const r of S.ledger) perKey[r.idempotency_key] = (perKey[r.idempotency_key] || 0) + 1;
  const count = (a) => S.ledger.filter((r) => r.action === a).length;
  const dupKeys = Object.values(perKey).filter((n) => n > 1).length;
  const dupRejected = S.metrics.duplicate_effects_rejected ?? 0;
  const dupPayments = S.ledger.filter((r) => r.action === "pay" && perKey[r.idempotency_key] > 1).length;
  $("ledger-counters").innerHTML =
    (dupKeys ? `<div class="counter bad wide"><span class="n">${dupKeys}</span><span class="l">DUPLICATE side effect${dupKeys > 1 ? "s" : ""}: same key performed twice${dupPayments ? ` (${dupPayments} payment rows)` : ""}</span></div>` : "") +
    [
      ["payments made", count("pay")], ["messages sent", count("send")],
      ["bookings made", count("reserve")], ["duplicates rejected", dupRejected],
    ].map(([l, n]) => {
      const bad = l === "payments made" && dupPayments;
      return `<div class="counter ${bad ? "bad" : ""}"><span class="n">${n}</span><span class="l">${l}</span></div>`;
    }).join("");
  $("ledger-rows").innerHTML = S.ledger.slice(-6).reverse().map((r) => {
    const dup = perKey[r.idempotency_key] > 1;
    return `<div class="lrow ${dup ? "dup" : ""}">${dup ? "DUPLICATE " : ""}${esc(r.action)} ${esc(r.receipt_id)}<br>key ${esc(r.idempotency_key)}</div>`;
  }).join("") || '<p class="hint">No side effects yet.</p>';
}

function rulesFor(link) {
  return [...S.faults, ...S.actionsFaults].filter((r) => r.link === link);
}

function renderLinks() {
  const bridgeUp = S.wsUp;
  const row = (label, link, procs) => {
    const rules = rulesFor(link);
    const down = !bridgeUp || procs.some((p) => procState(p) !== "online");
    const cut = rules.some((r) => r.action === "cut");
    const state = down ? "DOWN" : cut ? "CUT" : rules.length ? "FAULTS ARMED" : "connected";
    return `<div class="link ${state === "connected" ? "ok" : "bad"}"><span>${esc(label)}</span><span class="st">${state}</span></div>`;
  };
  const rows = [row("Agent A ↔ Bridge", "agent_a", ["agent_a"]), row("Bridge ↔ Agent B", "agent_b", ["agent_b"])];
  if (S.status.agent_b2 && S.status.agent_b2.status !== "stopped") rows.push(row("Bridge ↔ Agent B2", "agent_b2", ["agent_b2"]));
  rows.push(row("Workers → Actions", "actions", ["actions"]));
  $("links").innerHTML = rows.join("");
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
  recover.textContent = crashed.length ? `Recover ${crashed.join(", ")}` : "Recover Agent";
}

function dots(t) {
  const n = t.steps.length;
  const done = t.state === "COMPLETED" ? n : t.last_checkpoint ? t.last_checkpoint.step_index + 1 : 0;
  return "●".repeat(done) + "○".repeat(n - done);
}

function taskMeta(t) {
  if (t.state === "CREATED" && t.depends_on.length) return `waiting on task ${t.depends_on.join(", ")}`;
  if (t.state === "AWAITING_CONFIRMATION") return "waiting for confirmation";
  if (t.state === "DISPATCHED") return "ready; any worker may claim it";
  const bits = [];
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

function renderDialogs() {
  const html = S.dialogs.slice(-8).reverse().map((d) => {
    const rows = d.task_ids.map((id) => S.byId[id]).filter(Boolean).map((t) =>
      `<div class="trow" data-task="${esc(t.task_id)}" title="${esc(t.task_id)}">` +
      `<span>task ${t.index}</span><span>${esc(t.intent)}</span><span class="dots">${dots(t)}</span>` +
      `<span><span class="state ${esc(t.state)}">${esc(t.state)}</span></span>` +
      `<span class="tmeta">${taskMeta(t)}</span></div>`).join("");
    return `<div class="dialog"><div class="dialog-head">dialog ${esc(d.dialog_id)} · ${esc(d.msg_id)} · “${esc(d.text)}”</div>${rows}</div>`;
  }).join("");
  $("dialogs").innerHTML = html || '<p class="hint">No dialogs yet. Send a task to start one.</p>';
  renderLeases();
}

// --- rendering: Tests tab ------------------------------------------------------------------

function statusOf(id) {
  if (id === "rules") return worst(["r1", "r2", "r3", "r4"].map((r) => S.testStatus[r]));
  if (id === "m1") return worst(["m1_s1", "m1_s4"].map((r) => S.testStatus[r]));
  const st = S.testStatus[id];
  if (st) return st;
  if (S.run && S.run.state === "running" && (SELECTION_IDS[S.runSelect] || [S.runSelect]).includes(id)) return "RUNNING";
  return "NOT RUN";
}

function worst(list) {
  const known = list.filter(Boolean);
  if (!known.length) return S.run && S.run.state === "running" ? "RUNNING" : "NOT RUN";
  for (const s of ["TIMEOUT", "FAIL", "ERROR"]) if (known.includes(s)) return s;
  return known.length === list.length ? "PASS" : "RUNNING";
}

function evidenceFor(id) {
  const folder = id === "m1" ? "m1_s4" : id;
  for (const run of S.results) if (run.tests.includes(folder)) return { run: run.run_id, test: folder };
  return null;
}

function renderTests() {
  const rows = [];
  for (const [group, items] of TESTS) {
    rows.push(`<tr><th colspan="4">${esc(group)}</th></tr>`);
    for (const [id, name] of items) {
      const st = statusOf(id);
      const ev = evidenceFor(id);
      const evid = ev
        ? `<a href="${esc(url(port("supervisor"), `/results/${ev.run}/${ev.test}/summary.json`))}" target="_blank" rel="noopener">evidence</a> ` +
          `<button data-replay="${esc(ev.run)}|${esc(ev.test)}">Replay</button>`
        : "";
      rows.push(`<tr><td><b>${esc(id.toUpperCase())}</b> ${esc(name)}</td>` +
        `<td><span class="status ${st.replace(" ", "_")}">${esc(st)}</span></td>` +
        `<td><button data-run="${esc(id)}">Run</button></td><td>${evid}</td></tr>`);
    }
  }
  const table = $("tests-table");
  const html = rows.join("");
  if (table.dataset.html !== html) { table.innerHTML = html; table.dataset.html = html; }

  const run = S.run;
  $("run-state").textContent = run
    ? `${run.state === "running" ? "Running" : "Finished"} ${run.select} (${run.mode}${run.protection ? "" : ", protection OFF"})` +
      (run.state === "done" ? ` · exit ${run.exit_code}` : "")
    : "";
  if (run && run.output) {
    const out = $("run-output");
    out.textContent = run.output.join("\n");
    out.scrollTop = out.scrollHeight;
  }
  for (const b of document.querySelectorAll("[data-run], #btn-run-official, #btn-run-all, #btn-run-chaos")) {
    b.disabled = !!(run && run.state === "running");
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
  $("chaos-counters").innerHTML = c ? [
    ["tasks", c.tasks], ["faults applied", c.faults], ["crashes", c.crashes],
    ["duplicates rejected", c.dupRejected],
  ].map(([l, n]) => `<div class="counter"><span class="n">${esc(n)}</span><span class="l">${l}</span></div>`).join("") +
    `<div class="counter ${c.dupEffects ? "bad" : "good"}"><span class="n">${esc(c.dupEffects)}</span><span class="l">duplicate side effects (must stay 0)</span></div>` +
    `<div class="counter ${c.stuck ? "bad" : "good"}"><span class="n">${esc(c.stuck ?? "—")}</span><span class="l">stuck tasks</span></div>`
    : '<p class="hint">Press "Chaos run": 50 messages, random faults on every link, random crashes, two workers. It runs in the separate test world.</p>';
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

async function loadChaosFinal() {
  const ev = await api(port("supervisor"), `/results/${encodeURIComponent(S.run.run_id)}/c1/summary.json`);
  if (!ev.ok) return;
  const n = ev.data.numbers;
  S.chaosFinal = {
    tasks: n.tasks, faults: n.bridge_faults_applied, crashes: n.crashes,
    dupRejected: n.duplicate_effects_rejected, dupEffects: n.duplicate_side_effects, stuck: n.stuck_tasks,
    source: `results/${S.run.run_id}/c1 (${ev.data.status})`,
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

async function renderInspector() {
  const id = S.selectedTask;
  if (!id) return;
  const r = await api(port("bridge"), `/tasks/${encodeURIComponent(id)}/views`);
  if (!r.ok) return;
  $("view-mcp").innerHTML = json(r.data.mcp_style);
  $("view-shared").innerHTML = json(r.data.shared);
  $("view-a2a").innerHTML = json(r.data.a2a_style);
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
  const list = filteredEvents().slice(-300).reverse();
  $("eventlog").innerHTML = list.map((e) =>
    `<div class="ev ${groupOf(e.type)}"><span>${e.seq}</span><span>${esc(hhmmss(e.ts))}</span>` +
    `<span>${esc(e.type)}</span><span>${esc(e.actor)}</span>` +
    `<span class="data" title="${esc(JSON.stringify(e.data))}">${esc(short(e.task_id))} ${esc(JSON.stringify(e.data))}</span></div>`).join("") ||
    '<p class="hint">No events match.</p>';
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
    cells.map((c, i) => `<span class="fcell ${cls[i]}">${esc(c)}</span>`).join("") + "</div>";
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
  $("replay-event").innerHTML = `<h3>${esc(e.type)} · ${esc(e.actor)} · ${esc(hhmmss(e.ts))}</h3><pre class="code">${json(e)}</pre>`;
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
      $(`tab-${tab.dataset.tab}`).classList.remove("hidden");
      render();
      if (tab.dataset.tab === "inspect") renderInspector();
      if (tab.dataset.tab === "tests") loadResults();
    });
  }
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
  $("btn-reset").addEventListener("click", async () => {
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
  $("dialogs").addEventListener("click", (ev) => {
    const row = ev.target.closest(".trow");
    if (!row) return;
    S.selectedTask = row.dataset.task;
    document.querySelector('.tab[data-tab="inspect"]').click();
  });

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
      const [runId, test] = rep.dataset.replay.split("|");
      document.querySelector('.tab[data-tab="inspect"]').click();
      $("replay-run").value = runId;
      renderReplayTests();
      $("replay-test").value = test;
      loadReplay(runId, test);
    }
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
}

function restoreMode() {
  let mode = "fast";
  try { mode = localStorage.getItem("dashboard.mode") || "fast"; } catch (e) { mode = "fast"; }
  const btn = document.querySelector(`[data-mode="${mode}"]`);
  if (btn) btn.click();
}

async function start() {
  const r = await fetch("/config.json");
  S.cfg = await r.json();
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
