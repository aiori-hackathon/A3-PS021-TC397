# Dashboard

One web page served by the bridge at `http://localhost:8000`. Plain HTML, CSS
and JavaScript in `dashboard/`, with **no CDN**: the venue may be offline, so
every file, including fonts, ships in the repo (or system fonts are used).

## Look and feel

A calm control dashboard: a soft sky-blue background, white cards with a thin
border and a large soft shadow, black accents, and generous spacing.

- **Colour**: every colour, radius, spacing step, type size and timing is a
  token in `:root` of `style.css`; nothing else is used. Ink is near-black,
  secondary text is slate, the page is a vertical gradient from `#EEF6FD` to
  `#CFE5F7`.
- **Buttons**: primary actions are black pills with white text; everything else
  is a white pill with a thin border. Destructive buttons stay neutral and turn
  red only on hover.
- **Status colours** (green, amber, red, blue) appear only as small dots and
  badges, and always next to a word (`CRASHED`, `PASS`). **Dedup protection:
  OFF** is the one loud element: a warning banner across the top and a red
  border round the whole page.
- **Type**: system fonts only (`-apple-system, BlinkMacSystemFont, "Segoe UI",
  Inter, Roboto, sans-serif`; monospace for IDs, keys and JSON). Page title
  22 px, card titles 16 px, body 14 px. The only 12 px text is decorative
  uppercase labels (STEP chips, column headers, sender names); every status
  word and everything a judge reads is 14 px or larger.
- **Motion**: 150–200 ms transitions on hover and state changes, a short
  highlight when a ledger number changes, and a small slide when switching tabs.
  All of it is switched off under `prefers-reduced-motion`. Animations never
  change opacity, so a stalled animation cannot dim or hide content.

## Top bar

| Area | Contents |
| --- | --- |
| Left | Product name, `AIORI-3 · PS-021`, and `dialog-state/0.1-experimental (not a standard)` |
| Centre | **Live / Tests / Inspect** as a segmented control |
| Right | **Health pill** and the **Scripted / LLM** toggle |

The **health pill** is one short phrase: `4/4 online`, `Agent B crashed`,
`Bridge reconnecting`, or `Supervisor unreachable`. Hover or click it for the
per-service list (Bridge, Agent A, Agent B and B2 when running, Actions) with a
status word and "last heartbeat 0.4 s ago", plus the protection state. Escape
or a click elsewhere closes it.

Below the bar, only when needed: the **protection OFF banner**, the
**reconnecting / restarted banner**, and the black **caption bar** that shows
`test_step` events during watch-mode tests (only events from the last 20
seconds, so a caption replayed from history never looks live).

## Layout: three tabs

| Tab | For | Contents |
| --- | --- | --- |
| **Live** | Most of the demo | Control panel, chat, agent cards, ledger, links, dialogs and tasks |
| **Tests** | Results section | Test runner, group cards, chaos run, metrics, test output |
| **Inspect** | Judges' questions | Message inspector, message flow, event log, replay |

The Live tab must be understandable in five seconds. Depth lives one click away.

## Live tab

```
┌ Control panel ┐ ┌ Chat (hero) ───────────┐ ┌ Ledger  2×2 tiles ┐
│ STEP 1 Normal │ │ messages, plan pills,  │ ├ Links  diagram ───┤
│ STEP 2 Break  │ │ confirm / cancel       │ └───────────────────┘
│ STEP 3 Recover│ │ [ input ........ Send ]│
│ Advanced ▸    │ ├────────────────────────┤
└───────────────┘ │ Agent A │ Agent B      │
                  └────────────────────────┘
┌ Dialogs and tasks: table with a lifecycle stepper per task ────┐
```

The page grows with its content: at 1280×720 it scrolls vertically by a
little (the chat keeps at least 300 px); on a taller screen the chat takes the
spare height. There is never horizontal page scroll.

### Control panel
Grouped in demo order, each group under a black STEP chip:

| Group | Control | Calls |
| --- | --- | --- |
| STEP 1 · Normal run | **Send task** | Agent A `POST /message` (from the chat box) |
| STEP 2 · Break it | Crash-point select + **Crash agent B** (*kill now* / *arm: before pay*, *after pay call*, *after pay*) | Supervisor `/kill/agent_b` or `/restart/agent_b` with `CRASH_AT` |
| | **Disconnect A ↔ Bridge** (toggle) | Bridge `POST /faults` cut / `DELETE /faults/{id}` |
| | **More faults** (collapsible): drop next reply to A, slow Agent B, lose payment reply, clear all | Fault rules on the bridge and the actions service |
| STEP 3 · Recover | **Retry (same key)** | Agent A `POST /retry`: re-sends its last request with the same `request_id` |
| | **Recover agent** (names the crashed process; disabled when none) | Supervisor `/restart/<crashed process>` |
| Advanced (collapsed) | **Restart bridge** (hard kill + start), **Crash agent A**, **Reset demo** | Supervisor `/restart/bridge`, `/kill/agent_a`, `/reset` |

**Reset demo** needs a second click ("Click again to confirm"); it disarms
itself after 4 seconds.

### Chat (centre, hero card)
- Your messages are black bubbles on the right; Agent A's replies are light
  bubbles on the left; **bridge notices** have an amber left border; the final
  **summary** has a green one.
- Each detected task is a numbered line of pill tags: *payment · ₹40 · bakery ·
  scripted mode*.
- **Confirm / Cancel** buttons appear on side-effect tasks that wait for
  confirmation.
- Bridge notices read like *"agent_b stopped responding during EFFECT_PENDING.
  Task 1 suspended. The side effect may already have happened; it will only be
  retried with the same key."*
- The input bar stays at the bottom of the card and the chat scrolls to the
  newest message.

### Agent cards (A and B under the chat)
- A dot and a status word (`ONLINE`, `CRASHED`, `STARTING`, `STOPPED`).
- Current task and step, fence, attempt, and a lease countdown
  (*"lease expires in 1.6 s"*).
- A crashed agent shows the exit code and time, a red dot and a light red
  header; the rest of the card stays white.
- **Messages** (collapsible, keeps its open state): recent protocol messages in
  the agent's own format (JSON-RPC style for A, task/message style for B), with
  the same JSON highlighting as the Inspect tab.

### Ledger and links (right column)
- Ledger: four tiles: **payments made**, **messages sent**, **bookings made**,
  **duplicates rejected**. A tile briefly highlights when its number changes.
  If any idempotency key appears twice (only possible with protection off), a
  red **DUPLICATE side effect** tile leads the grid and the payments tile turns
  red. **Latest receipts** (collapsible) lists receipts with their keys.
- Links: a small diagram *A — Bridge — B — Actions*. A cut or down link shows ✕
  on its wire with a `CUT` / `DOWN` badge, armed fault rules show `!`; when all
  is well it reads "All links connected". Active fault rules are listed below
  with their remaining counts and notes.

### Dialogs and tasks (full width)
A table per dialog (newest expanded, older ones collapsed): task, intent, a
**horizontal lifecycle stepper**, the state badge, and details.

```
dialog dlg_3fa9…  "pay ₹40 to bakery and email receipt to Rahul"
  TASK    INTENT    LIFECYCLE            STATE            DETAILS
  task 1  payment   ●──●──●──●──◉──○     EFFECT_PENDING   step 4/5 · owner agent_b · fence 1 · attempt 1
  task 2  message   ◉──○──○──○──○        CREATED          waiting on task 1
```

The stepper runs CREATED → [AWAITING_CONFIRMATION] → DISPATCHED → IN_PROGRESS →
[EFFECT_PENDING] → COMPLETED. The current state is the ringed node; SUSPENDED
appears as an extra amber node after IN_PROGRESS; FAILED and CANCELLED replace
the last node. Hover a node for its state name. Click a task (or focus it and
press Enter) to open it in the Inspect tab.

## Tests tab

- **Test runner** card: pass tiles for **Official (x/5)**, **Extra**,
  **Interop** and **Rules**, each with a words line (*all passing*, *1
  failing*, *2 not run*); the **Fast / Watch** toggle; the **protection
  switch** (a green pill, red when off); a black **Run official five** button;
  **Run all** and **Chaos run** as secondary buttons.
- One card per group (*Official (S1–S5)*, *Baseline*, *Extra*, *Interop*,
  *Rules*, *Mutation*). Each row: ID, name, status badge (`PASS`, `FAIL`,
  `TIMEOUT`, `RUNNING` with a pulsing dot, `NOT RUN`) and one **Run** button.
  A **⋯** menu on the row holds **Evidence file** and **Replay in Inspect**.
- **Protection switch**: `Dedup protection: ON / OFF (testing only)`. Off
  resets the demo world with `DISABLE_DEDUP=1`; the whole page gets the
  protection OFF banner and border while off.
- **Chaos run**: tiles for tasks, faults applied, crashes and duplicates
  rejected, then a large **duplicate side effects (must stay 0)** tile (green
  at 0, red otherwise) and stuck tasks. The last chaos run's numbers are
  restored after a page reload.
- **Metrics** from `GET /metrics` as a two-column list, including recovery time
  versus baseline; **Test output** in its own card.

## Inspect tab

- **Message inspector**: choose a task in the card header. Three equal columns
  (tabs on narrow screens) show the MCP-style form, the shared schema record
  and the A2A-style form, with simple JSON highlighting (keys, strings,
  numbers). The fields that carry the lifecycle state in each form are
  outlined (MCP `structuredContent.state`, shared `state`, A2A `status.state`
  and `metadata.bridge_state`). SUSPENDED shows the `metadata.bridge_state`
  note.
- **Message flow**: four lanes (Agent A, Bridge, Agent B / B2, Actions); dropped
  messages end in ✕, repeats show ↺ *repeat* and *stored reply returned*.
  Hover a cell for its full text.
- **Event log**: a table with fixed columns (seq, time, type, actor, task, data),
  a sticky header and ellipsis. Click a row (or press Enter) to expand the full
  event. Filters by dialog, task, type group (lifecycle, protection, health,
  testing) and "rejections only" sit in one toolbar row; rows keep a coloured
  left edge by group (protection rows are red-tinted).
- **Replay**: load `results/<run>/<test>/events.json` and step through it with
  a slider. This is the backup if a live run fails on stage.

On screens 1500 px wide or more, the message flow and event log sit side by
side; below that they stack.

## States

Each of these is designed, not left to chance:

| State | What you see |
| --- | --- |
| Loading | Shimmering placeholders in the chat, dialogs, links and agent cards until the first data arrives |
| Empty | "No messages yet", "No dialogs yet", "No side effects yet", "No tests run yet", "No chaos run yet", "No events match", "No replay loaded" |
| Agent crashed | Red dot and `CRASHED` badge, light red card header, red health pill, ✕ on the link, **Recover agent** becomes a black primary button |
| Service not answering | An inline note in the affected card (Agent A → chat, actions service → ledger, bridge → dialogs); the Tests tab says when the supervisor is unreachable and disables Run |
| Bridge reconnecting | Amber top banner with a spinner and the health pill `Bridge reconnecting` |
| Bridge restarted | Green banner and a toast: *recovered N tasks; event numbers continue at N* |
| Protection OFF | Warning banner across the top, red page border, red `OFF` badge in the pill and the Tests tab, duplicate tiles in the ledger |
| Cannot load at all | A "Can't reach the Dialog Bridge" page with the command to start it and a **Try again** button |

## Live updates

Bridge `WS /ws`:

| Direction | Message |
| --- | --- |
| server → client, on connect | `{ "type": "hello", "boot_id": "...", "last_seq": 120 }` |
| client → server | `{ "type": "since", "seq": 97 }` |
| server → client | `{ "type": "event", "event": { ...event record... } }` (only after commit) |

Other data is polled once per second: supervisor `/status` and `/world`, bridge
`/tasks`, `/dialogs`, `/agents`, `/metrics` and `/faults`, actions `/ledger` and
`/faults`, and the agents' `/chat` and `/state`.

### Reconnect behaviour (required for S5)
1. On close, show *"Bridge restarting… reconnecting"* and retry every second.
2. On reconnect, compare `boot_id`; if it changed, show *"Bridge restarted
   — recovered N tasks"* (banner and toast) using the `bridge_started` event.
3. Send `since` with the last seen `seq`; append missed events in order.
4. Buttons that need the supervisor keep working while the bridge is down,
   because they call the supervisor directly (CORS is enabled for this origin).

## Implementation notes

- Files: `dashboard/index.html`, `app.js`, `style.css`, served by the bridge at
  `/` and `/static/…` with `Cache-Control: no-cache` (so a browser never shows
  an old copy after an update). System fonts only and inline SVG for icons;
  nothing is loaded from the internet (a test checks this).
- `GET /config.json` on the bridge tells the page every port, the lease time
  and the event groups, so nothing is hard-coded. Other services are called on
  the page's own hostname; `CORS_ORIGINS` allows both `localhost:8000` and
  `127.0.0.1:8000`.
- `app.js` finds elements by ID and a few `data-*` attributes (`data-tab`,
  `data-mode`, `data-run`, `data-replay`, `data-act`, `data-task`, `data-lease`,
  `data-view`); the layout can change freely as long as those stay.
- **Retry (same key)** calls Agent A `POST /retry`, which resends its last
  state-changing request with the same `request_id`; the bridge answers with
  the stored reply and logs `duplicate_request`.
- **Recover** restarts every process the supervisor reports as crashed.
- **Protection switch** resets the demo world with or without
  `DISABLE_DEDUP=1`; the state comes from the supervisor's `GET /world`.
- **Tests tab** starts runs through the supervisor (`POST /tests/run`), one at a
  time; progress comes from `GET /tests/{run_id}`, and statuses survive a page
  reload (`GET /tests`). Watch mode only runs world scenarios, against the demo
  world. *Run all* and *Chaos run* always run in fast mode in the test world;
  the chaos counters are read live from the test world, then replaced by the
  numbers in `results/<run>/c1/summary.json`. R1–R4 and M1 rows only show
  `RUNNING` when the current run includes them.
- **Replay** loads `events.json` from the supervisor (`GET /results/{run}/{test}/events.json`)
  and rebuilds task states and effect counts step by step.
- On WebSocket reconnect, if the bridge's `last_seq` is lower than what the
  page has seen, the world was reset: the page clears its event log and starts
  again from 0. Otherwise it catches up with `since`.
- The Live layout sets `--stack-h` from the height of the sticky top stack (bar
  and banners) so it fills the window whatever banners are showing.
- The **Scripted / LLM** toggle is shown but disabled until Phase 7.

## Accessibility and readability
- Contrast at least 4.5:1 for text; status is never shown by colour alone
  (always a word: CRASHED, SUSPENDED, PASS).
- Every button, select and disclosure is at least 40 px tall; focus rings are
  visible on every control; icon-only controls have an `aria-label`; task rows
  and event rows can be focused and opened with Enter.
- Works on a projector at 1280×720 and at 1920×1080 without horizontal
  scrolling; wide content (JSON, tables) scrolls inside its own card.
