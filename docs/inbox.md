# In-app inbox

In-app messages are **stored** per recipient and **pushed live** over SSE. The stored copy is the record: a recipient
who is offline, or whose connection drops, finds the message in their inbox when they return.

## How delivery works

1. The `in_app` attempt is dispatched. Pulse writes an `inbox_messages` row (one per attempt, so retries never
   duplicate it) and then publishes the live event to any connected SSE clients. The event carries `inbox_id`.
2. The attempt becomes `sent` once the message is stored. Nobody being connected is **not** a failure and is not retried.
3. The attempt becomes `delivered` when the recipient **reads** the message (or the client acks it, as before).
4. If it is still unconfirmed after `PULSE_RECONCILIATION__SSE_TTL_SECONDS` (default 120s), the reconciliation sweep
   escalates it to the workflow's fallback channel (default `in_app → push`). The message stays in the inbox.
   So "no push if they read it in time, push if they didn't" works without any extra configuration.

## API

All endpoints need the `client` scope and a `recipient` query parameter (the recipient's external reference).
A recipient that does not exist yet has an empty inbox.

| Method | Path | |
| :--- | :--- | :--- |
| `GET` | `/api/v1/inbox` | `state` = `active` (default, not archived), `unread`, `read`, `archived`, `all`; `limit` (≤100), `cursor` |
| `GET` | `/api/v1/inbox/unread-count` | The badge number |
| `POST` | `/api/v1/inbox/{id}/read` | Idempotent. Confirms delivery of the in-app attempt |
| `POST` | `/api/v1/inbox/{id}/unread` | |
| `POST` | `/api/v1/inbox/{id}/archive` · `/unarchive` | Archived messages leave the badge count |
| `POST` | `/api/v1/inbox/read-all` | Marks every non-archived unread message read |

`GET /inbox` returns `{items, next_cursor, unread_count}`, newest first. Pass `next_cursor` back as `cursor`
for the next page; it is `null` on the last page.

Each item: `id`, `notification_id`, `title`, `body`, `is_read`, `read_at`, `is_archived`, `created_at`.
Unknown ids, and ids belonging to another tenant or recipient, return `404`.

## Retention

The scheduler prunes hourly: messages older than `PULSE_RECONCILIATION__INBOX_RETENTION_DAYS` (90) and messages
archived more than `INBOX_ARCHIVED_RETENTION_DAYS` (7) days ago.

## Behaviour change from the SSE-only design

Previously an in-app message with no live connection **failed** and escalated to push straight away. Now it is stored,
and escalation happens only if it stays unread past the TTL. To get the old "push immediately when offline" behaviour,
set `PULSE_RECONCILIATION__SSE_TTL_SECONDS` low, or add `push` to the workflow's channels directly.
