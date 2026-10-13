# Sentinel contract

Pulse asks Sentinel (the user/tenant service) who an event is for. Sentinel does
not exist yet, so this is the contract Pulse speaks; adapt the endpoint or
mapping on whichever side is easier when the real service lands.

Enable it with `PULSE_SENTINEL__BASE_URL`. Without it, recipients come only from
the event payload and Pulse's local recipient table.

## Request

```
POST {base_url}{resolve_path}          (default path: /v1/recipients/resolve)
Authorization: Bearer {PULSE_SENTINEL__TOKEN}      (when a token is set)
X-Pulse-Tenant-Id: <tenant uuid>
Content-Type: application/json

{
  "tenant_id": "<tenant uuid>",
  "event_type": "user.signup",
  "payload": { ... the event payload, minus any inline "recipients" ... }
}
```

`PULSE_SENTINEL__PAYLOAD_KEYS` (a JSON list) limits which payload keys are
forwarded, for payloads that carry data Sentinel has no business seeing.

## Response

| Status | Meaning | Pulse does |
| --- | --- | --- |
| `200` `{"recipients": [...]}` | the people to notify | fans out; caches the answer |
| `404` | nobody matches | rejects the event (422); caches "nobody" briefly |
| `401` / `403` | Pulse's credentials are wrong | treats Sentinel as unavailable, no retry |
| `429` / `5xx` / timeout | Sentinel is struggling | retries, then falls back (below) |
| anything else, or an unusable body | contract violation | treats Sentinel as unavailable, no retry |

A recipient:

```json
{ "external_ref": "u_123", "email": "ada@example.com", "phone": "+14155550100",
  "locale": "en", "timezone": "Europe/Paris" }
```

Only `external_ref` is required (`locale` defaults to `en`, `timezone` to `UTC`).
More than `PULSE_SENTINEL__MAX_RECIPIENTS` (default 1000) recipients is rejected,
never silently truncated.

## What Pulse does when Sentinel is slow or down

1. **Cache.** Answers are cached per (tenant, event type, payload) for
   `CACHE_TTL_SECONDS` (60); "nobody" for `NEGATIVE_CACHE_TTL_SECONDS` (10).
   Identical concurrent lookups share one call.
2. **Retry** transient failures `RETRIES` (2) times with jittered exponential backoff.
3. **Circuit breaker.** After `BREAKER_FAILURE_THRESHOLD` (5) consecutive
   failures Sentinel is not called for `BREAKER_COOLDOWN_SECONDS` (30).
4. **Stale-if-error.** While Sentinel is down, the last good answer is served for
   up to `STALE_TTL_SECONDS` (1 hour).
5. **Local snapshot.** Failing that, a recipient named in the payload by
   `recipient_id` / `user_id` / `external_ref` is read from Pulse's own table
   (refreshed from Sentinel at every fan-out).
6. **Otherwise** the producer gets `503` with `Retry-After`. An unreachable
   Sentinel is never treated as "no recipients": the event is not dropped, and
   scheduled notifications simply wait for the next pass.

Re-sent events (same idempotency key) are answered from Pulse's own records
without calling Sentinel, so a producer's retries work during an outage.

Timeouts: `CONNECT_TIMEOUT_SECONDS` (2) and `READ_TIMEOUT_SECONDS` (5).
