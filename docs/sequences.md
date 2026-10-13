# Multi-step workflows (sequences)

A sequence sends a series of messages with delays and conditions, for example **in-app → wait 10 minutes → email if
it is still unread**. A workflow is a sequence when its definition has `steps`.

```json
{
  "steps": [
    {"id": "nudge", "channel": "in_app", "subject": "Hi {{ recipient.external_ref }}", "body": "Your order shipped", "format": "text"},
    {"id": "email", "channel": "email", "subject": "Your order shipped", "body": "Details inside…", "format": "markdown",
     "delay": "10m", "when": {"status": "unread"}},
    {"id": "text", "channel": "sms", "body": "Your order shipped", "format": "text",
     "delay": "1h", "when": {"step": "nudge", "status": "unread", "payload": [{"field": "total", "op": "gte", "value": 100}]}}
  ]
}
```

## Steps

| Field | Meaning |
| :--- | :--- |
| `id` | Lowercase letters, digits, `-`, `_` (max 40). Defaults to `step-N`. Unique within the workflow. |
| `channel`, `subject`, `body`, `format` | The message, exactly as in a normal workflow. Templates can use the event payload. |
| `delay` | How long **after the previous step ran** this one is due: seconds or `"30s"`, `"10m"`, `"2h"`, `"1d"` (max 30 days). Default 0. On the first step it delays the start. |
| `when` | Optional condition, checked when the step is due. If false the step is **skipped**. |

Each channel may appear in **one** step only (a recipient gets one delivery per channel per notification), up to 10 steps.
A workflow uses either `steps` or `channels` + `fallbacks`, not both: steps replace fallbacks.
Provider chains (`provider_chains`) work unchanged.

## Conditions (`when`)

All parts must hold.

- `status` tests an earlier step's message. `step` names which one; the default is the previous step.
  - `delivered` (alias `read`): the recipient got it. For in-app that means they read it or the client acked it; for
    email/SMS it means the provider confirmed delivery (or the provider has no receipts, so *sent* counts).
  - `not_delivered` (alias `unread`): not delivered yet, or it failed.
  - `failed`: it ended as failed or dead.
  - `skipped`: that step was skipped.
- `payload` is a list of checks on the event payload: `{"field": "order.total", "op": "gte", "value": 100}`.
  Operators: `eq`, `ne`, `in`, `not_in`, `exists`, `gt`, `gte`, `lt`, `lte`, `contains`. `field` may be dotted.
  A missing field never matches (except `ne`, `not_in` and `exists: false`).

**A step that depends on a skipped step is skipped too** (unless it asks for `skipped`), so a chain stops as soon as
someone has read the message: if the email was skipped because the in-app message was read, the SMS that tests the
email is skipped as well.

## How it runs

1. At ingest, step 1 runs for each recipient (unless delayed); its message goes through the normal outbox and workers.
   Later steps are stored as scheduled **step runs** (`workflow_step_runs`).
2. The scheduler checks for due steps every `PULSE_RECONCILIATION__STEP_POLL_SECONDS` (default 2s). It evaluates the
   condition, honours the recipient's opt-outs and quiet hours at that moment, creates the attempt and queues it,
   then schedules the next step.
3. A notification stays `processing` until every step has run and every attempt has settled. The reconciliation sweep
   does not give up on an unconfirmed message while a later step is still waiting.
4. `GET /api/v1/notifications/{id}` returns `steps` with each recipient's state (`scheduled`, `done`, `skipped`), when it
   ran, and the reason a step was skipped. The console shows the same under "Sequence steps".

## Things to know

- The delay is measured from when the previous step **ran**, not from when it was delivered. Choose a delay longer than the
  delivery time you expect: an in-app message still `pending` or `retrying` at that moment counts as not delivered.
- Editing a workflow affects notifications already in flight: steps are looked up by `id` when they run, and a step that
  no longer exists is skipped (with the reason recorded).
- Steps are per recipient. An event that resolves to several recipients gives each their own progress.
- Metrics: `pulse.workflow.steps` counts steps by `channel` and `outcome` (`done` / `skipped`).
