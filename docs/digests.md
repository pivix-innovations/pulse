# Digests (batching)

A digest turns a burst of events into **one** message per recipient: "5 new comments" instead of five emails.
Add a `digest` section to a normal workflow:

```json
{
  "channels": [
    {"channel": "email",
     "subject": "{% if digest.count > 1 %}{{ digest.count }} new comments{% else %}New comment{% endif %}",
     "body": "{% for c in digest.items %}- {{ c.author }}: {{ c.text }}\n{% endfor %}",
     "format": "text"}
  ],
  "digest": {"window": "10m", "max_items": 50, "key": "post_id"}
}
```

| Field | Meaning |
| :--- | :--- |
| `window` | How long after the **first** buffered event the digest is sent. Seconds or `"30s"`, `"10m"`, `"2h"`, `"1d"`; 1 second to 7 days. Required. |
| `max_items` | Send early once this many events are buffered (2 to 1000, default 100). |
| `key` | Optional payload field (dotted paths work). Events with different values digest separately, e.g. one digest per post. A missing field groups under an empty key. |

The window is fixed from the first event (a new event does not extend it).

## Templates

The message is rendered with the **latest** event's payload, plus a `digest` object:

- `digest.count`: how many events
- `digest.items`: every payload, oldest first
- `digest.first`, `digest.last`: the first and last payload

One template can cover both cases: with a single event `digest.count` is 1 and `digest.items` has one entry.
All channels of the workflow are digested together; use a separate workflow for events that must go out immediately.

## How it works

1. At ingest each recipient's event is stored in their open **batch** (one open batch per recipient, workflow and key).
   No message is created yet. The first event's notification is the batch's **anchor**.
2. The scheduler (every `PULSE_RECONCILIATION__STEP_POLL_SECONDS`, default 2s) sends every batch whose window has closed
   or that reached `max_items`. The consolidated message is an ordinary delivery attempt on the anchor
   notification: same providers, retries, fallbacks, quiet hours and opt-outs (checked when the digest is sent).
3. The other events' notifications complete when the digest is sent; they have no attempts of their own.
   Until then every notification involved stays `processing`.
4. `GET /api/v1/notifications/{id}` returns `digest` (`batch_id`, `status` open/flushed, `flush_at`, `item_count`,
   `is_anchor`, `anchor_notification_id`). The console shows the same.

## Things to know

- A recipient who has opted out of every channel by the time the digest is sent gets nothing, and the notifications complete.
- A digest whose template cannot be rendered is dropped (its notifications fail, with the error in the scheduler log)
  rather than retried forever.
- Re-sent events (same idempotency key) are not added twice.
- A workflow is either a digest or a sequence (`steps`), not both.
- Editing the workflow changes how batches that are already collecting are rendered when they are sent.
- Metrics: `pulse.digest.items` (events buffered) and `pulse.digest.flushes` (`outcome`: sent / failed).
