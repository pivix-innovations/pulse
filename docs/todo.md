# Pulse Engine: Audit Findings, Implementation Status & Market Roadmap

> **Document Status**: Audit Complete & Production Provider Milestone Verified  
> **Last Updated**: October 13, 2026  
> **Test Suite**: 222 passing, 1 skipped (`pytest`; the skipped test needs LocalStack, see `tests/integration/`)  
> **Linter**: 0 errors / 0 warnings (`ruff check .`)  
> **Runtime Startup**: `main.create_app()` boots cleanly (verified with the in-memory harness; not yet against live Postgres/Redis)  
> **Schema Migrations**: Linear head `e4c7f9a1b624` (10 revisions; SQL generated offline, not yet applied to a live database)

---

## Executive Status Tracker

### 1. Engine Core & Lifecycle (Phases 1–6)

| Task ID | Component / Area | Priority | Status | Verification & Implementation Summary |
| :--- | :--- | :--- | :--- | :--- |
| **TODO-1.1** | Notification Settlement | **Critical** | **[x] COMPLETED** | Created `services/settlement.py`. Wired in `ReconciliationService._settle()`, `api/v1/notifications.py` (`ack`), `api/v1/webhooks.py`, and `DeliveryService.deliver()` for immediate fire-and-forget settlement. |
| **TODO-1.2** | False Dead-Lettering Fix | **Critical** | **[x] COMPLETED** | Receipt-aware confirmation: `confirms_delivery = False` on SMTP and Console; dynamically set on Resend, SendGrid, Twilio and SES once their receipts are configured (signing key / public URL / SNS topic); `True` on FCM & SSE. Persisted in PostgreSQL. |
| **TODO-1.3** | Worker Claim Semantics | **Critical** | **[x] COMPLETED** | `attempt.mark_dispatching()` committed before provider send; `_requeue_stuck()` polls `list_stuck()` with `SKIP LOCKED` and re-enqueues outbox entries. |
| **TODO-2.1** | Dispatch Guard Lockout | **High** | **[x] COMPLETED** | `RedisDispatchGuard`: 180s in-flight lease on `acquire()`, 24h completed key on `complete()`, immediate deletion on `release()`. Crashed workers self-heal. |
| **TODO-2.2** | Database Providers Runtime | **High** | **[x] COMPLETED** | Replaced static `.env` resolver with dynamic `ProviderRegistry.resolve_chain()`. Loads per-tenant active providers, decrypts secrets, builds drivers dynamically, and records `provider_id`. |
| **TODO-2.3** | Recipient Resolution | **High** | **[x] COMPLETED** | `DatabaseRecipientClient` parses inline recipients or queries `recipients` table by `external_ref` (`recipient_id`/`user_id`/`external_ref`). Wired in `main.py` and `scheduler_worker.py`. |
| **TODO-3.1** | Quiet Hours Enforcement | **Medium** | **[x] COMPLETED** | `QuietHours.window_end()` handles standard and overnight windows. `fan_out()` sets `next_retry_at`, Outbox carries `defer_until`, and `OutboxRelay` schedules via Arq `_defer_by`. |
| **TODO-3.2** | Notification & Event Lists | **Medium** | **[x] COMPLETED** | Backend `GET /api/v1/notifications` & `GET /api/v1/events` with pagination and `X-Total-Count`. UI has a recent notifications table with status badges and clickable detail rows. |
| **TODO-3.3** | Dead-Letter Management API & UI | **Medium** | **[x] COMPLETED** | `api/v1/dead_letters.py` (`GET`, `POST /{id}/retry` redrive with guard release) + "Dead Letters" console tab with inline one-click Retry button. |
| **TODO-3.4** | SSE Auto-Provisioning | **Medium** | **[x] COMPLETED** | `api/v1/sse.py` auto-upserts missing recipients on connect to prevent premature 404s. |
| **TODO-4.1** | Ruff Lint Cleanup | **Low** | **[x] COMPLETED** | Removed unused imports; linter passes with 0 errors across the entire codebase. |
| **TODO-4.2** | Pydantic Warning Fix | **Low** | **[x] COMPLETED** | Added `json_format` alias in `config/logging.py` to eliminate Pydantic method shadowing warning. |
| **TODO-4.3** | UnitOfWork Protocol Sync | **Low** | **[x] COMPLETED** | Added identity repositories (`tenants`, `users`, `memberships`, `api_keys`, `sessions`) to `UnitOfWork` protocol & `InMemoryUnitOfWork`. |
| **TODO-4.4** | Identity Foreign Keys | **Low** | **[x] COMPLETED** | Added `ForeignKey` cascade constraints in `database/models/identity.py` and applied migration `b8d4f6a2c913`. |
| **TODO-4.5** | API Integration Test Suite | **Low** | **[x] COMPLETED** | Built comprehensive FastAPI `httpx.AsyncClient` test suite in `tests/api/` covering auth, events, notifications, dead letters, providers, webhooks, and SSE. |

---

### 2. Provider Catalog, Failover & Delivery Receipts (Phases 7–8)

| Task ID | Feature / Component | Priority | Status | Verification & Implementation Summary |
| :--- | :--- | :--- | :--- | :--- |
| **G1** | Provider Catalog Registry | **High** | **[x] COMPLETED** | Code registry (`providers/catalog.py`) with metadata, SVG logos, channel mappings, and field schemas. Endpoints: `GET /api/v1/provider-catalog` and logo asset route. |
| **G2** | Encrypted Secret Storage | **Critical** | **[x] COMPLETED** | Fernet symmetric encryption via `SecretBox` (`services/secrets.py`). Secrets stored encrypted in DB `providers.secrets_encrypted`; API is strictly write-only. |
| **G3** | Multiple Saved Providers | **High** | **[x] COMPLETED** | Multiple instances per type with custom labels, channel validation, and usage tracking (`used_by`). Migration `c1a5d7e9f402`. |
| **G4** | Workflow Provider Chains | **High** | **[x] COMPLETED** | `definition.provider_chains = {channel: [provider_id, ...]}` ordered by priority; validated on write; cascade protection prevents deleting in-use providers. |
| **G5** | In-Flight Channel Failover | **High** | **[x] COMPLETED** | `DeliveryService._send_through_chain()` fails over to next provider on timeouts and hard 5xx/auth errors. Persists trail on `attempt.last_error`. |
| **G6** | Provider Connection Testing | **Medium** | **[x] COMPLETED** | `POST /api/v1/provider-catalog/{channel}/{key}/test` tests credentials live for SMTP, Twilio, Resend, SendGrid, SES and FCM (dry run) before saving. |
| **G7** | Provider Console UI | **High** | **[x] COMPLETED** | Catalog picker modal with logos, credential forms, connection test buttons, and a drag-to-reorder provider chain editor. |
| **G8** | Production Provider Drivers | **High** | **[x] COMPLETED** | Amazon SES v2 (`ses.py`), Resend (`resend.py`), SendGrid (`sendgrid.py`), Twilio SMS (`twilio.py`) with live connection tests; FCM reworked (`firebase.py`). |
| **G9** | Signed Delivery Webhooks | **High** | **[x] COMPLETED** | Per-provider signed endpoints `POST /api/v1/webhooks/p/{provider_id}`. Verifies Twilio (HMAC-SHA1), Resend (Svix HMAC-SHA256), SendGrid (ECDSA) and SES-via-SNS (RSA) signatures. Migration `d2b6e8f0a513` stores `provider_message_id`. |
| **G11** | UI Failover Trail & Webhooks | **Medium** | **[x] COMPLETED** | Webhook URL panel with copy buttons on provider cards; attempt tables show provider logo, name, and failover trail. |
| **G10** | Amazon SES Delivery/Bounce via SNS | **Medium** | **[x] COMPLETED** | `parse_ses` in `providers/receipts.py` + `providers/sns.py`: verifies SNS signatures (certs only from AWS SNS hosts; message pinned to the provider's topic ARN), auto-confirms the subscription, maps Delivery / permanent Bounce / Reject. Real-SNS flow verified with LocalStack (`tests/integration`); SES v2 sending is unavailable in free LocalStack. |
| **G12** | Structured Workflow Editor | **Medium** | **[x] COMPLETED** | Form for channels, templates, fallbacks and drag-to-reorder provider chains; "Edit as JSON" retained. |
| **G13** | Provider Health | **Medium** | **[x] COMPLETED** | `provider_calls` log (outcome + latency; recipient errors excluded; pruned after 30 days), `GET /api/v1/providers/health`, status pills in the console. Migration `e4c7f9a1b624`. |
| **G14** | Provider Circuit Breaker | **High** | **[x] COMPLETED** | `RedisCircuitBreaker` (`jobqueue/circuit_breaker.py`): 5 consecutive provider failures open the circuit for 60s, then a single probe message tests recovery; shared across workers; recipient errors never count; if every circuit is open the chain is tried anyway; breaker faults never fail a delivery. State shown in provider health. Settings: `PULSE_BREAKER__*`. |
| **H1** | Push Error Classification | **High** | **[x] COMPLETED** | Classifies FCM errors: dead token (`UNREGISTERED`, sender mismatch), outage/quota (retry + fail over), bad credentials, and invalid request (`INVALID_ARGUMENT`: permanent, never prunes tokens). |
| **H2** | Stale Push Token Pruning | **High** | **[x] COMPLETED** | Deletes device tokens FCM reports dead, on success and on failure, so they are not retried on every send. |
| **H3** | Per-Provider FCM Instances | **High** | **[x] COMPLETED** | FCM instances initialized dynamically from provider credentials without global singleton collision. |

---

### 3. Active Backlog & Next Sprints

| ID | Item | Priority | Effort | Status | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **H4** | Unregister Device Endpoint | Medium | S | **[ ] OPEN** | `DELETE /api/v1/devices` (token in the body; tokens can be long) to remove a token on user logout. |
| **H5** | Push Notification Rich Payload Options | Medium | M | **[ ] OPEN** | Support notification blocks, custom sounds, badges, priority, and TTL. |
| **H6** | Apple Push Notification Service (APNs) | Medium | M | **[ ] OPEN** | Native APNs HTTP/2 driver using `.p8` token authentication. |
| **H7** | SSE Replay & Redis Fan-Out Test Seams | High | S | **[~] IN PROGRESS** | Unit tests for provider publish complete; stream replay and multi-instance fan-out tests need mock generator seam. |
| **H8** | Short-Lived Stream Tokens for SSE | Medium | M | **[ ] OPEN** | Replace API key in SSE URL query parameter with short-lived ticket/token. |
| **A1** | Full-Stack Smoke Test (`docker-compose`) | Critical | S | **[ ] OPEN** | Ingestion $\rightarrow$ Outbox $\rightarrow$ Worker $\rightarrow$ Delivery $\rightarrow$ Settlement integration verification script. |
| **A2** | Worker Claim Crash/Chaos Tests | Critical | M | **[ ] OPEN** | Test worker kill mid-dispatch; assert `_requeue_stuck()` recovers job when 180s lease expires. |
| **A3** | CI Pipeline (GitHub Actions) | High | S | **[ ] OPEN** | `.github/workflows/ci.yml` running `ruff check`, `pytest`, and `alembic upgrade head`. |
| **A4** | PostgreSQL-Backed Integration Tests | High | M | **[ ] OPEN** | Execute repository tests against real PostgreSQL instance to test `SKIP LOCKED` queries. |
| **C1** | Per-Tenant Rate Limiting & Send Quotas | High | M | **[ ] OPEN** | Redis token bucket algorithm on `POST /events`; enforce tenant daily send caps. |
| **C2** | Real Sentinel Service Integration | High | M | **[ ] OPEN** | Connect external auth/tenant microservice with caching, circuit breaker, and timeouts. |
| **C3** | Tenant Data Isolation Test Suite | High | S | **[ ] OPEN** | Integration tests asserting 404 on all cross-tenant resource access attempts. |
| **C4** | API Key Scopes & Rotation | Medium | M | **[ ] OPEN** | Ingest-only vs Admin scopes; key expiration and rotation mechanics. |
| **D1** | OpenTelemetry Distributed Tracing | Medium | L | **[ ] OPEN** | Propagate `traceparent` through Outbox entries and Arq jobs: HTTP $\rightarrow$ Relay $\rightarrow$ Worker $\rightarrow$ Webhook. |
| **D2** | Prometheus Metrics Exporter & Alerts | High | M | **[ ] OPEN** | `/metrics` endpoint with Prometheus gauges for queue depth, outbox lag, and dead-letter count. |
| **E1** | Persistent In-App Inbox Center | High | L | **[ ] OPEN** | `in_app_messages` table (`is_read`, `read_at`, `is_archived`); `GET /inbox`, `POST /inbox/{id}/read`, `POST /inbox/read-all`. |
| **E2** | Multi-Step DAG Workflows | High | L | **[ ] OPEN** | Ordered steps with conditional delays (e.g. In-App $\rightarrow$ wait 10m $\rightarrow$ if unread, send Email). |
| **E3** | Event Digest & Batching Engine | Medium | L | **[ ] OPEN** | Time-windowed aggregation buffer (e.g. batch 20 notifications into 1 summary email). |
| **E4** | Template Versioning & Preview | Medium | M | **[ ] OPEN** | Template draft/publish workflows with `POST /templates/{id}/preview` test render. |
| **E5** | Preference Center & Unsubscribe | Medium | M | **[ ] OPEN** | Self-service subscriber portal for category-based opt-outs. |
| **E6** | Chat Channels (Slack / Teams / Discord) | Low | M | **[ ] OPEN** | Native chat channel drivers and webhook worker. |

---

## Table of Contents

1. [Executive Summary & Current State](#1-executive-summary--current-state)
2. [Market Comparison & Competitive Gaps](#2-market-comparison--competitive-gaps)
3. [Verified Completed Milestones](#3-verified-completed-milestones)
   - [Phase 6: Delivery Lifecycle & Worker Claim Semantics](#phase-6-delivery-lifecycle--worker-claim-semantics)
   - [Phase 7: Architecture, Dispatch Guard & Recipient Resolution](#phase-7-architecture-dispatch-guard--recipient-resolution)
   - [Phase 8: Usability, Quiet Hours & Admin Console UI](#phase-8-usability-quiet-hours--admin-console-ui)
   - [Phase 9: Code Hygiene, Schema Integrity & API Test Harness](#phase-9-code-hygiene-schema-integrity--api-test-harness)
   - [Phase 10: Provider Catalog, Failover & Webhook Delivery Receipts](#phase-10-provider-catalog-failover--webhook-delivery-receipts)
4. [Actionable Implementation Guide for Open Items](#4-actionable-implementation-guide-for-open-items)
   - [Sprint 1: Reliability & CI](#sprint-1-reliability--ci)
   - [Sprint 2: Security & Rate Limiting](#sprint-2-security--rate-limiting)
   - [Sprint 3: In-App Message Center & Multi-Step DAGs](#sprint-3-in-app-message-center--multi-step-dags)

---

## 1. Executive Summary & Current State

Pulse is an enterprise-grade, event-driven notification engine built on Clean Architecture (FastAPI, SQLAlchemy Async, Redis, and Arq).

### Progress Verified (October 12–13, 2026)
Across 50+ commits over two days, the project advanced from a draft architecture with critical lifecycle bugs to a robust delivery engine with live production drivers and receipt handling:
- **Test Suite**: Expanded from 40 $\rightarrow$ 66 $\rightarrow$ **222 passing tests** in `pytest`.
- **Linter & Types**: **0 errors / 0 warnings** in `ruff check .`.
- **Database Migrations**: 10 linear revisions up to head `e4c7f9a1b624` (offline SQL generation only).
- **Production Drivers**: Amazon SES v2, Twilio SMS, Resend, SendGrid, Firebase Cloud Messaging, SMTP, SSE (HTTP-API drivers are covered by mocked-transport tests, not live-service tests).
- **Failover Engine**: Multi-provider fallback chain per workflow channel with error classification and failover audit trail.
- **Delivery Receipts**: Signed per-provider webhooks (Twilio, Resend, SendGrid, SES via SNS) with receipt-aware confirmation.
- **Admin Console**: Web UI for provider setup with live connection testing and health, a structured workflow editor, notification lookup with provider and failover trail, dead letters, and a live SSE stream.

---

## 2. Market Comparison & Competitive Gaps

| Dimension | Pulse Today | Market Leaders (Knock, Novu, Courier) | Actionable Gap |
| :--- | :--- | :--- | :--- |
| **Channels** | Email, SMS, Push, In-App (SSE). | Email, SMS, Push, Chat (Slack, Teams, Discord, WhatsApp), In-App. | Chat drivers (Slack/Teams) and APNs push driver. |
| **Provider Drivers** | Amazon SES, Resend, SendGrid, Twilio, FCM, SMTP, Console. | SES, SendGrid, Resend, Postmark, Twilio, Sinch, APNs, Expo, Slack. | APNs `.p8` driver, Sinch SMS, Postmark email. |
| **Provider Routing** | Per-tenant saved providers, encrypted secrets, ordered workflow chains with in-flight failover and per-provider health. | Provider routing rules, failover chains, weighted round-robin. | Add weighted round-robin routing. |
| **Delivery Receipts** | Per-provider signed webhooks for Twilio, Resend, SendGrid, and SES via SNS, with attempt matching. | Turnkey webhook ingestion for all major providers. | Postmark/Sinch receipt parsers when those drivers land. |
| **Workflows** | 1-step fan-out with provider chain failover and quiet hours. | Multi-step DAG workflows: delays, conditional branching, digests, throttling. | **E2**: Add multi-step DAG engine with delay steps. |
| **Digests & Batching** | None. Every event triggers immediate sends. | Turnkey digest engine (batch 20 actions into 1 daily/weekly email). | **E3**: Build outbox aggregation & digest window worker. |
| **In-App Experience** | Ephemeral SSE pipe with auto-ack and reconnect replay. | Persistent notification center, read/unread states, badges, React/Vue SDKs. | **E1**: Add `in_app_messages` store with read/unread flags and React SDK. |
| **Quiet Hours** | Local recipient timezone calculation, outbox deferral, Arq delayed dispatch. | Recipient & tenant quiet hours with delivery window holding. | **Completed**. |
| **Dead-Letter Ops** | Dead-letter list API & UI table with one-click retry (no search or filters yet). | Dead-letter inbox, automatic redrive policies, failure analytics. | Add automated redrive policies on transient provider recovery. |
| **Observability** | `GET /api/v1/metrics` channel/status matrix, per-provider health, and attempt failover trail. | OpenTelemetry end-to-end tracing, Prometheus metrics (`/metrics`). | **D1/D2**: Add OpenTelemetry spans and Prometheus metrics exporter. |

---

## 3. Verified Completed Milestones

### Phase 6: Delivery Lifecycle & Worker Claim Semantics

#### TODO-1.1: Notification Settlement
- **Status**: `[x] COMPLETED`
- **Files**: `services/settlement.py`, `services/reconciliation_service.py`, `services/delivery_service.py`, `api/v1/notifications.py`, `api/v1/webhooks.py`
- **Implementation**:
  - Centralized settlement logic in `services/settlement.py::settle_notification()`.
  - Transitions `Notification` to `COMPLETED` when all sister attempts are delivered/sent, or `FAILED` when attempts die.
  - Settle check executes immediately in `DeliveryService.deliver()` for fire-and-forget sends, on SSE client acknowledgment in `ack_notification()`, on provider delivery receipt callbacks in `webhooks.py`, and in the 30-second reconciliation sweep.
- **Verification**: `tests/test_reconciliation_service.py`, `tests/api/test_events_notifications.py`.

#### TODO-1.2: False Dead-Lettering Fix
- **Status**: `[x] COMPLETED`
- **Files**: `services/ports/providers.py`, `providers/catalog.py`, `services/delivery_service.py`, `repositories/delivery_repository.py`
- **Implementation**:
  - `confirms_delivery` added to `NotificationProvider` protocol.
  - Non-confirming providers (SMTP, SES, console) set `confirms_delivery = False`.
  - API providers (Resend, SendGrid, Twilio) dynamically set `confirms_delivery = True` only once webhook secrets are configured.
  - FCM and SSE set `confirms_delivery = True`.
  - `DeliveryService.deliver()` passes `expects_confirmation` to `attempt.mark_sent()`, persisted via `delivery_repository.py`.
- **Verification**: `tests/test_delivery_service.py`, `tests/test_receipts.py`.

#### TODO-1.3: Worker Claim Semantics
- **Status**: `[x] COMPLETED`
- **Files**: `services/delivery_service.py`, `services/reconciliation_service.py`, `config/reconciliation.py`
- **Implementation**:
  - In `DeliveryService.deliver()`, `attempt.mark_dispatching()` is committed before invoking the provider.
  - In `ReconciliationService.sweep_once()`, `_requeue_stuck()` queries `uow.delivery_attempts.list_stuck(...)` with `SKIP LOCKED`.
  - Jobs unclaimed for 300s or in-doubt for 300s (both longer than the 180s dispatch lease) are marked `mark_requeued()` and re-emitted to the Outbox.
- **Verification**: `tests/test_reconciliation_service.py::test_sweep_requeues_stuck_in_doubt_attempt`. Real-crash behaviour (kill a worker mid-send) is still untested: see A2.

---

### Phase 7: Architecture, Dispatch Guard & Recipient Resolution

#### TODO-2.1: Dispatch Guard Lockout Prevention
- **Status**: `[x] COMPLETED`
- **Files**: `jobqueue/dispatch_guard.py`, `services/delivery_service.py`
- **Implementation**:
  - `RedisDispatchGuard.acquire()` sets a 180s in-flight lease (`pulse:dispatch:{key}`).
  - `complete()` transitions the key to a 24h finished marker (`pulse:dispatched:{key}`).
  - `release()` immediately deletes the key on retryable failure.
  - Crashed workers release their lock automatically after 180 seconds, allowing stuck sweeps to retry safely.
- **Verification**: logic is exercised against `FakeDispatchGuard` only; the Redis implementation has no tests yet (see A2/A4).

#### TODO-2.2 & G1–G5: Database Provider Runtime & Failover Chain
- **Status**: `[x] COMPLETED`
- **Files**: `providers/registry.py`, `providers/catalog.py`, `services/secrets.py`, `services/delivery_service.py`
- **Implementation**:
  - Replaced static `.env` registry with dynamic `ProviderRegistry`.
  - Workflow definition specifies ordered provider chains: `definition.provider_chains = {"email": ["prov_1", "prov_2"]}`.
  - Secret credentials stored encrypted with Fernet app key (`SecretBox`).
  - `DeliveryService._send_through_chain()` iterates through the chain; on provider timeout or 5xx error, it fails over to the next provider and records the trail on `attempt.last_error`.
- **Verification**: `tests/test_provider_registry.py`, `tests/test_delivery_failover.py`, `tests/test_secrets.py`.

#### TODO-2.3: Recipient Resolution Upgrade
- **Status**: `[x] COMPLETED`
- **Files**: `clients/sentinel_client.py`, `main.py`, `workers/scheduler_worker.py`
- **Implementation**:
  - `DatabaseRecipientClient` parses inline recipient payloads if present.
  - Falls back to querying `uow.recipients.get_by_external_ref(tenant_id, ref)` using `recipient_id`, `user_id`, or `external_ref`.
- **Verification**: `tests/test_notification_service.py::test_database_recipient_client_falls_back_to_stored_recipient`.

---

### Phase 8: Usability, Quiet Hours & Admin Console UI

#### TODO-3.1: Quiet Hours Enforcement
- **Status**: `[x] COMPLETED`
- **Files**: `domain/value_objects/quiet_hours.py`, `services/notification_service.py`, `jobqueue/relay.py`
- **Implementation**:
  - `QuietHours.window_end(reference_dt)` calculates end of quiet window across midnight boundaries.
  - `NotificationService.fan_out()` evaluates quiet hours against recipient local timezone; if covered, stamps `attempt.next_retry_at` and passes `defer_until` to Outbox.
  - `OutboxRelay` computes `defer_by_seconds` and passes `_defer_by` to the Arq queue.
- **Verification**: `tests/test_notification_service.py` (`test_quiet_hours_defer_attempt_and_outbox_publish`, `test_quiet_hours_window_end_wraps_midnight_and_respects_timezone`).

#### TODO-3.2, 3.3, 3.4 & G7, G11: Console UI & Endpoints
- **Status**: `[x] COMPLETED`
- **Files**: `ui/index.html`, `api/v1/notifications.py`, `api/v1/events.py`, `api/v1/dead_letters.py`, `api/v1/sse.py`, `api/v1/providers.py`
- **Implementation**:
  - `GET /api/v1/notifications` & `GET /api/v1/events` with pagination and `X-Total-Count`.
  - `GET /api/v1/dead-letters` and `POST /api/v1/dead-letters/{id}/retry` redrive endpoint.
  - `api/v1/sse.py` auto-provisions missing recipient records on connect.
  - Console UI (`ui/index.html`):
    - Provider catalog picker modal with logos, credential fields, and live connection test.
    - Provider cards showing webhook URLs with copy button, receipts on/off state and live health.
    - Structured workflow editor with drag-to-reorder provider chains.
    - Recent notifications list table with pagination, status filter, and detail modal.
    - Dead-letter management table with error details and one-click Retry button.
    - Live SSE test stream with auto-ack and reconnect replay.
- **Verification**: `tests/api/test_events_notifications.py`, `tests/api/test_dead_letters.py`, `tests/api/test_providers.py`, `tests/api/test_sse.py`.

---

### Phase 9: Code Hygiene, Schema Integrity & API Test Harness

#### TODO-4.1 to 4.5: Cleanliness, Migrations & Integration Suite
- **Status**: `[x] COMPLETED`
- **Files**: `services/identity_service.py`, `config/logging.py`, `services/ports/uow.py`, `database/models/identity.py`, `tests/api/`
- **Implementation**:
  - Removed unused imports (`UserId`).
  - Fixed Pydantic field shadowing in `LoggingSettings`.
  - Added identity repositories to `UnitOfWork` protocol and `InMemoryUnitOfWork`.
  - Added cascade foreign keys in `b8d4f6a2c913_phase5_identity_foreign_keys.py`.
  - Built comprehensive FastAPI `httpx.AsyncClient` test harness in `tests/api/conftest.py` with 40+ API integration tests.
- **Verification**: `ruff check .`, `pytest tests/api/`.

---

### Phase 10: Provider Catalog, Failover & Webhook Delivery Receipts

#### G1–G9, G11, H1–H3: Production Drivers, Failover & Delivery Callbacks
- **Status**: `[x] COMPLETED`
- **Files**:
  - `providers/catalog.py` & `providers/assets/`
  - `providers/sns.py`, `services/provider_health.py`, `repositories/provider_call_repository.py`
  - `providers/email/ses.py` (Amazon SES v2 with error categorization)
  - `providers/email/resend.py` (Resend email API)
  - `providers/email/sendgrid.py` (SendGrid v3 API)
  - `providers/sms/twilio.py` (Twilio SMS API)
  - `providers/push/firebase.py` (FCM v1 with per-provider apps and token cleanup)
  - `providers/receipts.py` (Webhook signature verification & receipt parser)
  - `api/v1/webhooks.py` (Shared per-provider signed webhook endpoints)
- **Implementation**:
  - `POST /api/v1/webhooks/p/{provider_id}` verifies incoming webhooks against the specific provider's secret:
    - **Twilio**: HMAC-SHA1 over full URL and sorted POST parameters.
    - **Resend**: Svix timestamp and HMAC-SHA256 signature verification.
    - **SendGrid**: ECDSA public key signature verification over timestamp + payload.
  - Stored `provider_message_id` on delivery attempts (migration `d2b6e8f0a513`) for asynchronous receipt matching.
  - In-flight failover logs the failure trail on `attempt.last_error`.
  - FCM error classification: dead tokens (`UNREGISTERED`, sender mismatch) are deleted; `INVALID_ARGUMENT` is permanent but never prunes tokens (FCM does not say whether token or payload was at fault); outages/quota retry and fail over.
  - SES delivery events arrive through SNS (`providers/sns.py`): signature, certificate-host and topic checks, automatic subscription confirmation.
  - Every provider call is logged (`provider_calls`) and aggregated into health (`services/provider_health.py`).
- **Verification**: `tests/test_http_providers.py`, `tests/test_ses_provider.py`, `tests/test_fcm_provider.py`, `tests/test_receipts.py`, `tests/test_sns_receipts.py`, `tests/test_provider_health.py`, `tests/api/test_provider_webhooks.py`, `tests/api/test_ses_webhook.py`, `tests/integration/test_localstack_ses.py` (needs `LOCALSTACK_URL`).

---

## 4. Actionable Implementation Guide for Open Items

### Sprint 1: Reliability & CI

#### A3: GitHub Actions CI Pipeline
- **Priority**: High | **Effort**: S
- **Implementation**:
  Create `.github/workflows/ci.yml`:
  ```yaml
  name: CI
  on: [push, pull_request]
  jobs:
    test:
      runs-on: ubuntu-latest
      services:
        postgres:
          image: postgres:16-alpine
          env:
            POSTGRES_DB: pulse_test
            POSTGRES_USER: pulse
            POSTGRES_PASSWORD: secretpassword
          ports: ["5432:5432"]
          options: >-
            --health-cmd pg_isready
            --health-interval 10s
            --health-timeout 5s
            --health-retries 5
        redis:
          image: redis:7-alpine
          ports: ["6379:6379"]
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with: { python-version: "3.10" }
        - run: pip install uv && uv sync
        - run: uv run ruff check .
        - run: uv run pytest   # in-memory fakes; needs no services
        - run: uv run alembic upgrade head
          env:
            # Settings require database credentials; point them at the service above.
            PULSE_DATABASE__DRIVER: postgresql+asyncpg
            PULSE_DATABASE__HOST: localhost
            PULSE_DATABASE__PORT: "5432"
            PULSE_DATABASE__USERNAME: pulse
            PULSE_DATABASE__PASSWORD: secretpassword
            PULSE_DATABASE__DB: pulse_test
  ```

#### A1 & A2: Full-Stack Smoke & Chaos Tests
- **Priority**: Critical | **Effort**: M
- **Implementation**:
  1. Add end-to-end integration test in `tests/test_chaos.py` simulating worker termination mid-dispatch (`SIGKILL` during `dispatch_started_at`).
  2. Verify that once the 180s dispatch lease has expired and the attempt has been in doubt for `stuck_in_doubt_ttl_seconds` (300s), the sweep requeues it. Use short TTLs in the test and a real Redis for the guard (the guard is only tested against a fake today).

---

### Sprint 2: Security & Rate Limiting

#### C1: Per-Tenant Rate Limiting & Quotas
- **Priority**: High | **Effort**: M
- **Problem**: A rogue tenant can overwhelm outbox relays and provider APIs with event flooding.
- **Implementation**:
  1. Implement Redis Token Bucket in `jobqueue/rate_limiter.py`.
  2. In `api/v1/events.py`, evaluate rate limit before accepting event:
     - Return `HTTP 429 Too Many Requests` with `Retry-After` header when bucket is empty.
  3. Support per-tenant daily quota limits stored on the `tenants` table.

#### H4: Device Token Unregistration Endpoint
- **Priority**: Medium | **Effort**: S
- **Implementation**:
  1. Add `DELETE /api/v1/devices` (body: `{"token": ...}`) in `api/v1/devices.py`.
  2. Deletes the token from `device_tokens` for the acting tenant (tokens can be long, so pass it in the body rather than the path).

---

### Sprint 3: In-App Message Center & Multi-Step DAGs

#### E1: Persistent In-App Notification Center
- **Priority**: High | **Effort**: L
- **Problem**: SSE in-app notifications are ephemeral. If a user is not online or disconnects, the notification cannot be retrieved later in a UI inbox.
- **Implementation**:
  1. Add `in_app_messages` database table:
     - Columns: `id`, `tenant_id`, `recipient_id`, `notification_id`, `title`, `body`, `data` (JSONB), `is_read` (bool), `read_at` (timestamp), `is_archived` (bool), `created_at`.
  2. Add REST Endpoints:
     - `GET /api/v1/inbox`: List recipient messages with unread count and pagination.
     - `POST /api/v1/inbox/{id}/read`: Mark specific message read.
     - `POST /api/v1/inbox/read-all`: Mark all unread messages as read.
     - `DELETE /api/v1/inbox/{id}`: Archive message.
  3. When an `in_app` delivery attempt succeeds, insert an `in_app_messages` record.

#### E2: Multi-Step DAG Workflow Engine
- **Priority**: High | **Effort**: L
- **Problem**: Workflows currently fan out to all channels simultaneously or step down on timeout. They cannot express "Send In-App $\rightarrow$ Wait 15 minutes $\rightarrow$ If unread, send Email".
- **Implementation**:
  1. Define workflow DAG node types: `channel_dispatch`, `delay`, `condition` (`is_read`, `channel_failed`).
  2. Add workflow execution state machine in `services/workflow_executor.py`.
  3. Schedule delayed workflow hops via Arq background tasks.
