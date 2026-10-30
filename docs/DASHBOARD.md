# Dashboard

One web page served by the bridge at `http://localhost:8000`. Plain HTML, CSS
and JavaScript in `dashboard/`, with **no CDN**: the venue may be offline, so
every file, including fonts, ships in the repo (or system fonts are used).

## Layout: three tabs

| Tab | For | Contents |
| --- | --- | --- |
| **Live** | Most of the demo | Controls, chat, agent panels, dialog/task view, link status, ledger, captions |
| **Tests** | Results section | Test runner, mode switch, protection switch, chaos run, metrics, evidence links |
| **Inspect** | Judges' questions | Message inspector, message flow, event log with filters, replay |

The Live tab must be understandable in five seconds. Depth lives one click away.

## Live tab

### Header
- Title, `dialog-state/0.1-experimental (not a standard)` label.
- **Status chips** from the agent registry and supervisor: `Bridge :8000 online`,
  `Agent A online`, `Agent B crashed`, `Actions online`, each with
  "last heartbeat 0.4 s ago".
- **Reconnecting banner** when the WebSocket drops (see below).

### Control strip
Numbered in demo order:

| # | Button | Calls |
| --- | --- | --- |
| 1 | Send Task | Agent A `POST /message` (from the chat box) |
| 2 | Crash Agent B ▾ (*kill now* / *arm crash point*: before pay, after pay call, after pay) | Supervisor `/kill/agent_b` or `/restart/agent_b` with `CRASH_AT` |
| 3 | Disconnect A ↔ Bridge (toggle) | Bridge `POST /faults` cut / `DELETE /faults/{id}` |
| 4 | Retry (same key) | Agent A re-sends its last request with the same `request_id` |
| 5 | Recover Agent | Supervisor `/restart/<crashed agent>` |

Secondary: **Restart Bridge** (hard kill + start), **Reset Demo**, **Crash Agent A**.

### Chat (centre, widest)
- User messages and Agent A replies.
- After each message: *"Detected intent: payment · ₹40 · bakery · scripted mode"*.
- **Confirm / Cancel** buttons on side-effect tasks.
- Bridge notices in a highlighted style: *"Agent B stopped responding during
  EFFECT_PENDING. Task suspended. No new payment until the ledger is checked."*
- Header toggle: **Scripted / LLM**. LLM is disabled with a tooltip if Ollama is not reachable.

### Agent panels (left: Agent A, right: Agent B)
- Recent protocol messages in the agent's own format (JSON-RPC style on the
  left, task/message style on the right).
- Current step, fence, lease countdown (*"lease expires in 1.6 s"*).
- A crashed agent's panel turns to a warning colour with the exit time.

### Caption bar
Above the panels. Shows `test_step` events during watch-mode tests:
*"Setting trap: Agent B crashes after paying" → "Restarting Agent B" →
"Checking ledger: 1 payment ✓"*.

### Dialog and task view
```
dialog dlg_3fa9…  "pay ₹40 to bakery and email receipt to Rahul"
 ├─ task 1  payment   ●●●●○  EFFECT_PENDING   owner agent_b · fence 1 · attempt 1
 └─ task 2  message   ○○○○○  CREATED          waiting on task 1
```
Click a task to open it in the Inspect tab.

### Link status
Small diagram: A ↔ Bridge ↔ B → Actions, each link green or red, plus the
active fault rules with remaining counts and their notes.

### Ledger card
- Big counters: **payments made**, **messages sent**, **bookings made**,
  **duplicates rejected**.
- Latest receipts with their idempotency keys.
- When protection is off, duplicate rows are shown in red.

## Tests tab
- **Run** per test and **Run official five** / **Run all**; mode switch **Fast / Watch**.
- Table grouped as *Official (S1–S5)*, *Baseline*, *Extra*, *Interop*, *Rules*;
  statuses `PASS`, `FAIL`, `TIMEOUT`, `RUNNING`, `NOT RUN`.
- Link to each test's evidence folder; button to open its replay.
- **Protection switch**: `Dedup protection: ON / OFF (testing only)`. Off
  restarts the world with `DISABLE_DEDUP=1`; the whole page gets a warning
  border while off.
- **Chaos run** button with live counters: tasks, faults applied, crashes,
  duplicates rejected, **duplicate side effects** (must stay 0).
- **Metrics panel** from `GET /metrics`, including recovery time vs baseline.

## Inspect tab
- **Message inspector**: select a task or message; three columns show the
  MCP-style form, the shared envelope, and the A2A-style form. SUSPENDED shows
  the `metadata.bridge_state` note.
- **Message flow**: a live sequence view (A, Bridge, B, Actions as lanes);
  dropped messages end in ✕, duplicates appear twice with the second marked
  *rejected*.
- **Event log**: filters by dialog, task, type group (lifecycle, protection,
  health, testing) and "rejections only"; colour by group.
- **Replay**: load `results/<run>/<test>/events.json` and step through it with
  a slider. This is the backup if a live run fails on stage.

## Live updates

Bridge `WS /ws`:

| Direction | Message |
| --- | --- |
| server → client, on connect | `{ "type": "hello", "boot_id": "...", "last_seq": 120 }` |
| client → server | `{ "type": "since", "seq": 97 }` |
| server → client | `{ "type": "event", "event": { ...event record... } }` (only after commit) |

Other data is polled once per second: supervisor `/status`, agents' `/state`,
actions `/ledger`, bridge `/metrics` and `/faults`.

### Reconnect behaviour (required for S5)
1. On close, show *"Bridge restarting… reconnecting"* and retry every second.
2. On reconnect, compare `boot_id`; if it changed, show *"Bridge restarted
   — recovered N tasks"* using the `bridge_started` event.
3. Send `since` with the last seen `seq`; append missed events in order.
4. Buttons that need the supervisor keep working while the bridge is down,
   because they call the supervisor directly (CORS is enabled for this origin).

## Accessibility and readability
- Text at least 14 px; contrast at least 4.5:1; status never shown by colour alone
  (always a word: CRASHED, SUSPENDED).
- Works on a projector at 1280×720 without horizontal scrolling.
