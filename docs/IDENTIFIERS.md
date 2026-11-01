# Identifiers

Every ID answers one question. Mixing them up is the most common way to break
deduplication, so each one has exactly one creator and one purpose.

## Summary

| ID | Example | Created by | When | Answers the question |
| --- | --- | --- | --- | --- |
| `msg_id` | `msg_17` | Agent A (or the test / dashboard sending the message) | When a user message arrives | Which user message started this? |
| `dialog_id` | `dlg_3fa91c07d2e1` | **Bridge** | On `dialog.create` | Which conversation is this part of? |
| `task_id` | `tsk_8c1f0a2b9e44` | **Bridge** | On `dialog.create`, one per task | Which unit of work is this? **Never changes.** |
| `request_id` | `msg_17:create`, `tsk_8c…:checkpoint:2:1` | The sender | Before sending; saved or derivable | Have I already processed this exact request? |
| `effect_key` | `tsk_8c1f0a2b9e44:pay` | Agent B, derived | Before a side effect | Has this side effect already happened? |
| `fence` | `1`, `2`, `3` | **Bridge** | +1 on every successful claim or resume | Is this sender still the current owner? |
| `attempt` | `1`, `2`, `3` | **Bridge** | +1 on every claim after the first | How many times has this task been started? |
| `seq` | `42` | **Bridge** | +1 on every event | What happened, in what order? What did I miss? |
| `agent_id` | `agent_a`, `agent_b`, `agent_b2` | Configuration | At start | Who is speaking? |
| `receipt_id` | `rcp_51d0a8` | Actions service | When a side effect is performed | Proof the side effect happened |
| `run_id` | `2026-11-02_14-30-05` | Test runner | At the start of a test run | Which run produced this evidence? |

The bridge generates five of these: `dialog_id`, `task_id`, `fence`, `attempt`, `seq`.

## Formats

- `dialog_id` = `"dlg_" + 12 hex chars` from `uuid4()`
- `task_id` = `"tsk_" + 12 hex chars` from `uuid4()`
- `receipt_id` = `"rcp_" + 12 hex chars` from `uuid4()`
- `seq` = SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`, so it never goes backwards, even after a restart
- In test mode, tests pass fixed `msg_id` values (`s4`, `s5`, …) so every derived key is predictable

## The derivation rules (the important part)

Keys are **built from IDs that survive a crash**, never random. A restarted
agent rebuilds exactly the same key without having to remember it.

| Request | `request_id` / key |
| --- | --- |
| Agent A creates a dialog | `{msg_id}:create` |
| Agent A confirms a task | `{task_id}:confirm` |
| Agent A cancels a task | `{task_id}:cancel` |
| Agent registers | `{agent_id}:register:{boot_id}` |
| Worker claims next task | `{agent_id}:claim_next:{boot_id}:{poll_counter}` |
| Worker claims a specific task (S2) | `{agent_id}:claim:{task_id}:{boot_id}` |
| Worker resumes | `{agent_id}:resume:{boot_id}` |
| Worker checkpoints | `{task_id}:checkpoint:{step_index}:{fence}` |
| Worker records effect intent | `{task_id}:effect_intent:{fence}` |
| Side effect (to actions) | `{task_id}:{action}` — **no fence, no attempt** |
| Worker reports effect result | `{task_id}:effect_result:{fence}` |
| Worker completes | `{task_id}:complete:{fence}` |
| Worker fails a task | `{task_id}:fail:{fence}` |
| Heartbeat | `{agent_id}:hb:{boot_id}:{counter}` |

Why the effect key has no fence or attempt: a second attempt by a new owner
must produce the **same** key, so the actions service recognises it as the
same side effect. Adding the attempt number would turn a retry into a "new"
payment.

Why checkpoint and complete keys include the fence: a new owner's checkpoint
for step 2 is a different request from the old owner's checkpoint for step 2.

Why the counters carry the agent's `boot_id`: poll and heartbeat counters live
in memory and restart at 1. Without the `boot_id`, a restarted worker's first
`claim_next` would reuse its previous life's key and get that life's stored
reply back (an old task with an old fence). `boot_id` is a fresh random value
per process start; it only scopes the counter, it is never a key on its own.

## Common mistakes

| Mistake | Result |
| --- | --- |
| `request_id = uuid4()` on every send | Retries look new, so deduplication silently does nothing |
| Effect key includes the attempt | A recovered worker pays again |
| Agent creates `task_id` itself | Two agents can create two IDs for the same work |
| Using `sent_at` for leases | Clock differences between processes cause false expiries; leases use the bridge's clock only |
