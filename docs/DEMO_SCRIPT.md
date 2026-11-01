# Demo Script

Target: **10 minutes** plus questions. Two presenters: **P1** talks, **P2**
drives the dashboard. Swap halfway if you like.

## Before you go on stage (checklist)

- [ ] Laptop on charger; sleep and notifications off; projector at 1280×720 tested
- [ ] `python supervisor.py --mode demo` running for at least 5 minutes; all chips online
- [ ] **Reset Demo** pressed; ledger shows 0
- [ ] `TEST_MODE=fast pytest` passed on this laptop today; results folder ready
- [ ] Browser zoom set so the Live tab fits without scrolling
- [ ] Backup: screen recording of the full demo on the desktop and on a USB stick
- [ ] Backup: a recent run's evidence loaded in the Replay panel
- [ ] Pseudocode slide (the S4 test) open in another tab
- [ ] No internet needed (no CDN, no cloud); Wi-Fi can be off
- [ ] If showing LLM mode: Ollama running, model already pulled, tried once

## Timeline

| Time | Who | What to show | What to say (key line) |
| --- | --- | --- | --- |
| 0:00 | P1 | Title slide | "When agents hand work to each other and something breaks, work gets lost or done twice. Done twice is dangerous when it's a payment." |
| 0:45 | P1 | Architecture diagram | "Two agents never talk directly. Everything goes through our Dialog Bridge, which keeps every task's state in a database that survives crashes." |
| 1:45 | P2 | Live tab: type *"Pay ₹40 to the bakery and email the receipt to Rahul"* | "The router found two tasks. The email waits for the payment. Notice the same dialog ID on both." |
| 2:15 | P2 | Confirm; watch both tasks complete; ledger: 1 payment, 1 email | "That's the normal path. Now let's break it." |
| 3:00 | P2 | Arm crash point *after pay call*; send *"pay ₹40 to the bakery"*; Agent B dies right after paying | "The money moved, but the bridge never heard back. This is the worst moment to crash." |
| 3:30 | P1 | Point at the lease countdown → SUSPENDED | "No heartbeat, so the lease expires. The task is suspended, not lost." |
| 3:45 | P2 | Recover Agent | "Agent B remembers nothing. It asks the bridge where it was, and retries the payment with the same key." |
| 4:15 | P1 | Ledger: **1 payment**, "duplicates rejected: 1"; same task ID | "The payment service recognised the key and returned the original receipt. Same task, one payment." |
| 4:45 | P2 | Tests tab: protection **OFF**, run S4 in watch mode | "Now with our protection switched off." |
| 5:30 | P1 | Ledger shows **2 payments** in red | "Two payments. That's the bug we prevent." Switch protection back **ON**. |
| 6:00 | P2 | Restart Bridge (hard kill) during a task, with A disconnected | "Now we kill the bridge itself, mid-task." |
| 6:30 | P1 | Banner: reconnecting → "Bridge restarted, recovered N tasks"; task completes | "Everything reloads from SQLite. The event numbers continue with no gap." |
| 7:15 | P2 | Tests tab: run all in fast mode | "All five required scenarios plus our extras, in about a minute." |
| 8:00 | P1 | Inspect tab: one task in MCP-style, shared, A2A-style views | "The same task in both protocol styles. One honest finding: A2A-style has no 'suspended' state, so we carry it in metadata." |
| 8:45 | P2 | Chaos run counters | "Fifty tasks, random failures everywhere, and zero duplicate side effects." |
| 9:30 | P1 | Limitations slide | "This is an experimental schema, not a standard. We don't claim full MCP or A2A, or exactly-once delivery in general." |
| 10:00 | both | — | Questions |

## If something goes wrong

| Problem | Do this |
| --- | --- |
| A process won't start | Supervisor **Reset Demo**; if still broken, switch to the recording |
| A scenario misbehaves live | "Let me show the recorded run from this morning" → Replay panel |
| Dashboard frozen | Reload the page; state is all in the bridge, so nothing is lost |
| Projector resolution wrong | Browser zoom 80% |
| LLM mode slow or failing | Switch back to Scripted; "the bridge never depends on the model" |

## Likely questions and short answers

| Question | Answer |
| --- | --- |
| Are these real AI agents? | No, they're scripted mock adapters with a rule-based intent router, as the problem statement asks. Scripting makes every test repeatable. LLM mode exists for Agent A, but correctness never depends on it. |
| Is this exactly-once delivery? | No. Messages can be delivered more than once; we make repeats harmless. Side effects through our actions service happen at most once per key. |
| What if the actions service ignored keys? | Then layer 4 is gone; layers 1–3 still reduce duplicates but cannot rule them out. Real payment APIs commonly support idempotency keys for this reason. |
| Why SQLite? | Durable, zero setup, one file. One bridge process is our stated assumption. |
| What if two bridges ran? | Out of scope; it would need a shared database or consensus. Listed in limitations. |
| Why do keys not include the attempt number? | So a retry by a different worker is recognised as the same side effect. |
| How do you know your tests actually test anything? | We turn protection off and show they fail (2 payments). |
| How does this relate to the IETF work? | We followed the agentproto discussions for inspiration only; this is an experimental schema, labelled on every message. |
