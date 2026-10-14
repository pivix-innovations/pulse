# Pulse Backlog

> Follows [todo.md](todo.md), where every audit item (TODO-1.1 → 4.5) is complete.
> Status: `[ ]` open · `[~]` in progress · `[x]` done. Effort: S (<1d), M (1–3d), L (>3d).

## Phase A — Verify & Harden (do first)

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| A1 | Full-stack smoke test via `docker-compose` ✅ | Critical | S | Ingest → outbox → worker → deliver → settle; also retry, fallback escalation and dead-letter paths. Last commit changed settlement, claim semantics, dispatch guard and quiet hours together. |
| A2 | Crash/chaos tests for worker claim & requeue ✅ | Critical | M | Kill a worker mid-`dispatching`; assert `_requeue_stuck()` recovers and the dispatch guard's 180s lease expires. |
| A3 | CI pipeline (GitHub Actions) ✅ | High | S | `ruff check`, `pytest`, `alembic upgrade head` on Postgres + Redis services. No `.github/` yet. |
| A4 | Postgres-backed repository tests ✅ | High | M | Current tests run on in-memory fakes; add tests against real SQL for outbox, delivery and settlement queries. |
| A5 | Repo hygiene ✅ | Low | S | Add `.DS_Store` to `.gitignore`; remove tracked `__pycache__`; refresh `todo.md` header. |

## Phase B — Production Providers

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| B1 | SMS: Twilio driver ✅ | High | M | Only a console driver exists. Include status webhooks (HMAC-verified) and set `confirms_delivery` correctly. |
| B2 | Email: SES / Resend / SendGrid driver ✅ | High | M | SMTP is fire-and-forget; API drivers give bounce/delivery webhooks. |
| B3 | Push: APNs (HTTP/2, `.p8` token) | Medium | M | FCM exists. |
| B4 | Provider secrets handling ✅ | High | M | DB `providers.config` holds non-secret values only; add encrypted secret storage or a secret-manager reference. |
| B5 | Bounce/complaint suppression list ✅ | Medium | M | Stop sending to hard-bounced addresses and unsubscribed numbers. Console page and the unsubscribe link (E5) still to do. |

## Phase C — Security & Multi-Tenant Safety

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| C1 | Per-tenant rate limiting & quotas ✅ | High | M | Redis token bucket on `POST /events`; per-tenant daily send caps. |
| C2 | Sentinel client: replace the stub ✅ | High | M | `clients/sentinel_client.py` is in-memory; wire the real user/tenant lookup with caching and timeouts. |
| C3 | Tenant isolation tests ✅ | High | S | Assert every repository query and API route is tenant-scoped (cross-tenant read/write attempts return 404). |
| C4 | API key rotation & scopes ✅ | Medium | M | Expiry, last-used tracking, revocation, read-only vs ingest scopes. |
| C5 | Audit log ✅ | Medium | M | Who changed workflows, templates, providers, API keys. `GET /api/v1/audit-log` (admin); console page still to do. |

## Phase D — Observability

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| D1 | OpenTelemetry tracing | Medium | L | Propagate `traceparent` through outbox entries and arq payloads: HTTP → relay → worker → webhook. |
| D2 | Metrics & alerts (OpenTelemetry → OpenObserve) ✅ | High | M | Outbox lag, queue depth, stuck attempts, dead-letter rate, per-provider error rate and latency. OTLP export, so D1 tracing reuses it; see [observability.md](observability.md). |
| D3 | Structured log correlation ✅ | Medium | S | Carry `notification_id`, `tenant_id` and `attempt_id` on every log line. Done for ingest, delivery and receipt webhooks (`telemetry/log_context.py`); format follows `PULSE_LOGGING__JSON` (JSON by default). |
| D4 | Runbook | Low | S | Dead-letter replay, stuck-outbox recovery, provider failover. |
| D5 | OpenObserve alerts & dashboards as code | Medium | S | Ship the eight alert rules from [observability.md](observability.md) and an overview dashboard as importable JSON, plus a script that applies them through the OpenObserve API. |
| D6 | Ship logs to OpenObserve (OTLP logs) | Medium | S | Same pipeline as metrics; pairs with D3 so every line carries `notification_id`/`tenant_id`. |

## Phase E — Product Features (market parity)

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| E1 | Persistent in-app inbox ✅ | High | L | `in_app_messages` table (`is_read`, `read_at`, `is_archived`); `GET /inbox`, `POST /inbox/{id}/read`, `POST /inbox/read-all`. SSE is live-only today. |
| E2 | Multi-step workflows ✅ | High | L | Ordered steps with delays and conditions (e.g. in-app → wait 10m → if unread, email). Builds on E1. |
| E2b | Visual sequence builder in the console | Low | M | Steps, delays and conditions are edited as JSON today; add a form with a timeline preview. |
| E2c | Cancel a running sequence | Low | S | Stop remaining steps for a notification (API + console button). |
| E3 | Digest / batching ✅ | Medium | L | `digest` step with time windows or thresholds; buffer table plus a consolidated render. |
| E3b | Digest extras: rolling windows, daily-at-9am schedule, per-channel digests | Low | M | Today the window is fixed from the first event and covers every channel of the workflow. |
| E4 | Template versioning & preview ✅ | Medium | M | Draft/publish, rollback, `POST /templates/{id}/preview` with sample data. |
| E4b | Template diff view and per-version usage | Low | S | Show a text diff between versions in the console; record which version each message was rendered from. |
| E5 | Subscriber preference center [~] | Medium | M | Per-category opt-out and the unsubscribe link/endpoint that B5 needs. Unsubscribe link and endpoint shipped (`{{ unsubscribe_url }}`, `/api/v1/unsubscribe`); per-category opt-out still open. |
| E6 | Chat channels (Slack/Teams webhook) | Low | M | New `Channel` enum value plus worker. |

## Phase F — Console UI & DX

| ID | Item | Pri | Effort | Notes |
| :--- | :--- | :--- | :--- | :--- |
| F1 | Notification timeline view | Medium | M | Show delivery attempts, provider, fallback hops and settlement per notification. |
| F2 | Event/notification search | Medium | M | Filter by recipient, workflow, status and date range, beyond the status filter. |
| F3 | Provider and workflow editor UI | Medium | M | API exists; add console pages. |
| F4 | OpenAPI polish + client SDK | Low | M | Examples, error schemas, generated Python/TS SDK. |
| F5 | Headless inbox widget (JS/React) | Low | L | Builds on E1 (done): list, badge, read, archive, live updates via SSE. |
| F6 | Console: browse a recipient's inbox | Low | S | Support view over `GET /inbox` with read/unread state; useful for debugging "I never got it". |

## Phase G — Provider Catalog & Workflow Provider Chains

Clients pick a provider from a catalog (name + logo), configure it **once** as a saved provider, then attach saved providers to a workflow in priority order.

| ID | Item | Pri | Effort | Status | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| G1 | Provider catalog (code registry) | High | M | [x] | Name, logo, channel, config/secret field schema, driver factory per entry. `GET /api/v1/provider-catalog`, logo endpoint. |
| G2 | Encrypted secret storage | Critical | M | [x] | Fernet app-level key; secrets write-only (API returns only which are set). |
| G3 | Saved providers (multiple instances per type) | High | M | [x] | `catalog_key` + `label` + `config` + encrypted secrets; schema validation against the catalog. |
| G4 | Workflow provider chains | High | M | [x] | `definition.provider_chains = {channel: [provider_id, ...]}` ordered by priority; validated on write; providers in use can't be deleted. |
| G5 | Failover inside a channel | High | M | [x] | Next provider only on hard errors and timeouts (never on sent-but-unconfirmed). When every provider in the channel fails, the existing channel fallback applies. |
| G6 | Provider connection test | Medium | S | [x] | `POST /provider-catalog/{channel}/{key}/test` (works on unsaved forms, reuses stored secrets); "Test connection" button in the console. Live check for SMTP, Twilio, Resend, SendGrid, FCM (dry run). |
| G7 | Console UI: catalog grid with logos, configure form, drag-to-prioritise chain editor | High | L | [~] | Catalog picker, configure form and ↑/↓ chain editor shipped; drag-to-reorder and "test connection" still open. |
| G8 | Real providers: Twilio, Resend, SendGrid, SES | High | M | [x] | All four in the catalog with live connection tests. |

## Phase H — SSE & Push hardening

| ID | Item | Pri | Effort | Status | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| G9 | Delivery receipts (webhooks) for Twilio, Resend, SendGrid | High | M | [x] | Per-provider signed endpoint `POST /webhooks/p/{provider_id}`; attempts keep the provider message id; confirmation is only expected once receipts are set up, so unconfigured providers never get falsely escalated. Needs `PULSE_API__PUBLIC_URL` for Twilio. |
| G10 | SES delivery/bounce events via SNS | Medium | M | [x] | Verifies SNS signatures (cert from AWS SNS hosts only, topic pinned to the provider's ARN) and auto-confirms the subscription. Verified against real SNS signatures with LocalStack (`tests/integration`, set `LOCALSTACK_URL`); SES sending itself needs real AWS (LocalStack SES v2 is Pro). |
| G11 | UI: webhook URL panel, provider column and failover trail on attempts | Medium | S | [x] | |
| G12 | UI: structured workflow editor with drag-to-reorder chains | Medium | M | [x] | Channels, templates, fallbacks and provider chains in a form; "Edit as JSON" kept for anything else. |
| G13 | Provider health (success rate, latency, last failure) | Medium | M | [x] | Every provider call is logged (outcome + latency; recipient errors excluded) and pruned after 30 days; `GET /providers/health`; status pills in the console. |
| G14 | Circuit breaker: skip a provider that is down, probe it after a cooldown | High | M | [x] | Builds on G13 so failover doesn't pay the timeout on every message while a provider is out. |
| H1 | Push error classification (retryable / permanent / failover) | High | S | [x] | Timeouts and 5xx fail over; invalid tokens do not. |
| H2 | Deactivate stale device tokens on `UNREGISTERED` | High | S | [x] | Stop retrying dead tokens on every send. |
| H3 | FCM per-provider app instances (no process-global init) | High | S | [x] | Credentials come from the saved provider's secrets. |
| H4 | Unregister device endpoint | Medium | S | [ ] | `DELETE /devices`. |
| H5 | Push payload options: notification block, TTL, priority, badge | Medium | M | [ ] | |
| H6 | APNs driver | Medium | M | [ ] | |
| H7 | SSE: tests for replay, ack-to-settle, no-subscriber failure, fan-out | High | S | [x] | Stream body extracted (`stream_events`); unit, API, real-Redis and real-stack tests. |
| H8 | SSE: short-lived stream token instead of API key in the URL | Medium | M | [ ] | |
| H9 | Persistent inbox (see E1) | High | L | [x] | See [inbox.md](inbox.md). |

## Suggested order

1. **A1–A3** (confidence before more features)
2. **C1, C2, D2** (production readiness)
3. **B1, B2, B4** (real delivery)
4. **E1 → E2 → E3** (differentiating features)
5. Everything else as capacity allows
