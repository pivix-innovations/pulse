# Pulse Engine: Audit Findings, Implementation Status & Market Roadmap

> **Document Status**: Audit Complete — Implementation & Review Verified  
> **Last Updated**: October 12, 2026  
> **Test Suite**: 66/66 passing (`pytest`)  
> **Linter**: 0 errors (`ruff check .`)  
> **Schema Migrations**: Linear head `b8d4f6a2c913` (all 7 revisions verified)

---

## Executive Status Tracker

| Task ID | Component / Area | Priority | Status | Verification & Implementation Summary |
| :--- | :--- | :--- | :--- | :--- |
| **TODO-1.1** | Notification Settlement | **Critical** | **[x] COMPLETED** | Created `services/settlement.py`. Wired in `ReconciliationService._settle()`, `api/v1/notifications.py` (`ack`), and `api/v1/webhooks.py`. |
| **TODO-1.2** | False Dead-Lettering Fix | **Critical** | **[x] COMPLETED** | `confirms_delivery = False` on SMTP & Console; `True` on FCM & SSE. Wired in `DeliveryService.deliver()` & `delivery_repository.py`. |
| **TODO-1.3** | Worker Claim Semantics | **Critical** | **[x] COMPLETED** | `mark_dispatching()` committed before provider send; `_requeue_stuck()` polls `list_stuck()` and re-enqueues outbox entries. |
| **TODO-2.1** | Dispatch Guard Lockout | **High** | **[x] COMPLETED** | `RedisDispatchGuard`: 180s in-flight lease on `acquire()`, 24h completed key on `complete()`, immediate deletion on `release()`. |
| **TODO-2.2** | Database Providers Runtime | **High** | **[x] COMPLETED** | `ProviderRegistry.resolve(uow, tenant_id, channel)` picks the tenant's highest-priority active `providers` row (name selects the driver, non-secret `config` overrides env settings), else the env default. `DeliveryService` records `provider_id` on `mark_sent`. |
| **TODO-2.3** | Recipient Resolution | **High** | **[x] COMPLETED** | Created `DatabaseRecipientClient` with fallback to `recipients` table by `external_ref`; wired in `main.py` and `scheduler_worker.py`. |
| **TODO-3.1** | Quiet Hours Enforcement | **Medium** | **[x] COMPLETED** | Implemented `QuietHours.window_end()`. `fan_out()` sets `next_retry_at`, Outbox carries `defer_until`, and `OutboxRelay` computes `defer_by_seconds`. |
| **TODO-3.2** | Notification & Event Lists | **Medium** | **[x] COMPLETED** | `GET /api/v1/notifications` and `GET /api/v1/events` with pagination & `X-Total-Count`; console Notifications page has a paginated, status-filterable recent list with clickable rows. |
| **TODO-3.3** | Dead-Letter Management API | **Medium** | **[x] COMPLETED** | `api/v1/dead_letters.py` (`GET`, `POST /{id}/retry`) plus a "Dead Letters" console page with inline Retry. |
| **TODO-3.4** | SSE Auto-Provisioning | **Medium** | **[x] COMPLETED** | `api/v1/sse.py` auto-upserts missing recipient on connect to prevent premature 404s. |
| **TODO-4.1** | Ruff Lint Cleanup | **Low** | **[x] COMPLETED** | Removed unused `UserId` import in `services/identity_service.py`. Linter passes with 0 warnings. |
| **TODO-4.2** | Pydantic Warning Fix | **Low** | **[x] COMPLETED** | Added `json_format` with `alias="json"` in `config/logging.py` to prevent method shadowing. |
| **TODO-4.3** | UnitOfWork Protocol Sync | **Low** | **[x] COMPLETED** | Added identity repositories (`tenants`, `users`, `memberships`, `api_keys`, `sessions`) to `UnitOfWork` protocol & `InMemoryUnitOfWork`. |
| **TODO-4.4** | Identity Foreign Keys | **Low** | **[x] COMPLETED** | Added `ForeignKey` cascade constraints in `database/models/identity.py` and applied migration `b8d4f6a2c913`. |
| **TODO-4.5** | API Router Test Suite | **Low** | **[x] COMPLETED** | `tests/api/` runs the real app over in-memory fakes via `httpx.ASGITransport`: auth/session/API-key, event ingest + idempotency, lists, ack settlement, dead-letter list/retry, SSE auth + auto-provisioning. |
| **POLISH** | Immediate Settlement on Deliver | **Low** | **[x] COMPLETED** | `DeliveryService.deliver()` settles the notification in the same transaction after a fire-and-forget send. |

---

## Table of Contents

1. [Executive Summary & Current State](#1-executive-summary--current-state)
2. [Market Comparison & Competitive Gaps](#2-market-comparison--competitive-gaps)
3. [Priority 1: Critical Engine Fixes (Phase 6 Completion)](#3-priority-1-critical-engine-fixes-phase-6-completion)
   - [TODO-1.1: Wire Notification Settlement](#todo-11-wire-notification-settlement-notificationsettle--list_settleable) `[x] COMPLETED`
   - [TODO-1.2: Fix False Dead-Lettering of SMTP & Console Deliveries](#todo-12-fix-false-dead-lettering-of-smtp--console-deliveries) `[x] COMPLETED`
   - [TODO-1.3: Wire Worker Claim Semantics](#todo-13-wire-worker-claim-semantics-mark_dispatching--list_stuck) `[x] COMPLETED`
4. [Priority 2: Reliability & Architecture Fixes](#4-priority-2-reliability--architecture-fixes)
   - [TODO-2.1: Prevent 24-Hour Dispatch Lockout on Worker Crash](#todo-21-prevent-24-hour-dispatch-lockout-on-worker-crash) `[x] COMPLETED`
   - [TODO-2.2: Connect Database `providers` Table & API to Dispatch Runtime](#todo-22-connect-database-providers-table--api-to-dispatch-runtime) `[x] COMPLETED`
   - [TODO-2.3: Upgrade Recipient Resolution Beyond In-Memory Stub](#todo-23-upgrade-recipient-resolution-beyond-in-memory-stub) `[x] COMPLETED`
5. [Priority 3: Functional Gaps & Usability](#5-priority-3-functional-gaps--usability)
   - [TODO-3.1: Enforce Recipient Quiet Hours](#todo-31-enforce-recipient-quiet-hours) `[x] COMPLETED`
   - [TODO-3.2: Add Notification & Event List/Search Endpoints](#todo-32-add-notification--event-listsearch-endpoints) `[x] COMPLETED`
   - [TODO-3.3: Implement Dead-Letter Management API & UI Replay](#todo-33-implement-dead-letter-management-api--ui-replay) `[x] COMPLETED`
   - [TODO-3.4: Auto-Provision Recipient on SSE Stream Connect](#todo-34-auto-provision-recipient-on-sse-stream-connect) `[x] COMPLETED`
6. [Priority 4: Code Hygiene & Protocol Integrity](#6-priority-4-code-hygiene--protocol-integrity)
   - [TODO-4.1: Clean Up Ruff Lint Errors & Warnings](#todo-41-clean-up-ruff-lint-errors--warnings) `[x] COMPLETED`
   - [TODO-4.2: Resolve Pydantic Field Shadowing Warning in LoggingSettings](#todo-42-resolve-pydantic-field-shadowing-warning-in-loggingsettings) `[x] COMPLETED`
   - [TODO-4.3: Synchronize `UnitOfWork` Protocol with Identity Repositories](#todo-43-synchronize-unitofwork-protocol-with-identity-repositories) `[x] COMPLETED`
   - [TODO-4.4: Add Foreign Key Constraints to Identity Schema](#todo-44-add-foreign-key-constraints-to-identity-schema) `[x] COMPLETED`
   - [TODO-4.5: Add API Router Test Suite with FastAPI `TestClient`](#todo-45-add-api-router-test-suite-with-fastapi-testclient) `[x] COMPLETED`
7. [Priority 5: Strategic Market Parity Roadmap](#7-priority-5-strategic-market-parity-roadmap)

---

## 1. Executive Summary & Current State

Pulse is an event-driven, multi-tenant notification engine built on Clean Architecture (FastAPI, SQLAlchemy Async, Redis, and Arq).

### Audit & Verification Results (October 12, 2026)
Following the initial audit of Phase 6 delivery lifecycle gaps, fixes were applied across the engine:
- **Test Suite**: Expanded from 40 to **66 tests, all passing** (`pytest`).
- **Linter**: Clean with **0 errors and 0 warnings** (`ruff check .`).
- **Startup**: `main.create_app()` boots cleanly with **0 warnings**.
- **Alembic**: Linear migration tree verified up to head `b8d4f6a2c913`.
- **Engine Core**: Notification settlement, worker claim semantics, quiet hours deferral, and in-flight lease management are verified working.

### Remaining Open Gaps Summary
None from this audit. Only the Priority 5 market-parity roadmap remains.

---

## 2. Market Comparison & Competitive Gaps

| Dimension | Pulse Today | Market Leaders (Knock, Novu, Courier) | Actionable Gap |
| :--- | :--- | :--- | :--- |
| **Channels** | Email (SMTP), Push (FCM v1), SMS (console), In-App (SSE). | Email (SES, SendGrid, Postmark, Resend), SMS (Twilio, Sinch), Push (APNs, Expo), Chat (Slack, Teams, Discord, WhatsApp). | Needs real SMS driver (Twilio) and APNs support. |
| **Provider Routing** | Static `.env` configuration. DB table is ignored. | Per-tenant provider credentials, automatic provider failover (SendGrid $\rightarrow$ Resend), round-robin. | **TODO-2.2**: Wire the DB `providers` table into the dispatch pipeline. |
| **Workflow Engine** | 1-step fan-out + fallback on timeout. | Multi-step DAG workflows: delays, conditional branching, digests, throttling. | Add batching/digest engine and step delays. |
| **Digests & Batching** | None. Every event triggers immediate sends. | Turnkey digest engine (batch 20 actions into 1 daily/weekly email). | Build outbox aggregation & digest window worker. |
| **In-App Experience** | Ephemeral SSE pipe. Replay on reconnect. | Persistent notification center, read/unread states, badges, React/Vue SDKs. | Add in-app message store with read/unread flags. |
| **Quiet Hours** | Fully calculated & deferred via Outbox Relay. | Enforces quiet hours, holds messages until window ends. | **Completed (TODO-3.1)**. |
| **Dead-Letter Ops** | Backend API & Redrive implemented. UI missing. | Searchable dead-letter inbox, redrive policies, manual one-click UI retry. | **TODO-3.3**: Add dead-letter tab to `ui/index.html`. |
| **Observability** | Basic count aggregates on `/metrics/summary`. | OpenTelemetry end-to-end tracing, Prometheus metrics (`/metrics`). | Add OpenTelemetry spans and Prometheus exporter. |

---

## 3. Priority 1: Critical Engine Fixes (Phase 6 Completion)

### TODO-1.1: Wire Notification Settlement (`Notification.settle` & `list_settleable`)
- **Status**: **`[x] COMPLETED`**
- **Severity**: Critical
- **Implementation Details**:
  - Created reusable helper `services/settlement.py::settle_notification(uow, tenant_id, notification_id)`.
  - Wired into `ReconciliationService._settle()` for periodic background reconciliation sweeps of settleable notifications.
  - Wired into `api/v1/notifications.py::ack_notification()` to immediately settle notifications when SSE client acknowledges delivery.
  - Wired into `api/v1/webhooks.py::provider_webhook()` to immediately settle notifications when external provider DLR webhooks arrive.
  - Added unit test in `tests/test_reconciliation_service.py::test_reconciliation_settles_notification_when_attempts_terminal` verifying full transition to `COMPLETED` and `FAILED`.
- **Remaining Minor Polish**:
  - In `DeliveryService.deliver()`, when a delivery succeeds on a provider where `expects_confirmation=False` (e.g. SMTP/Console), call `await settle_notification(uow, attempt.tenant_id, attempt.notification_id)` immediately instead of waiting for the 30-second reconciliation sweep.

---

### TODO-1.2: Fix False Dead-Lettering of SMTP & Console Deliveries
- **Status**: **`[x] COMPLETED`**
- **Severity**: Critical
- **Implementation Details**:
  - Added `confirms_delivery: bool` attribute to `NotificationProvider` protocol in `services/ports/providers.py`.
  - Set `confirms_delivery = False` on `SmtpEmailProvider`, `ConsoleEmailProvider`, `ConsoleSmsProvider`, and `ConsolePushProvider`.
  - Set `confirms_delivery = True` on `FirebasePushProvider` and `SseInAppProvider` (require delivery confirmation).
  - Updated `DeliveryService.deliver()` to check `provider.confirms_delivery` and set `expects_confirmation` on `attempt.mark_sent()`.
  - Fixed `repositories/delivery_repository.py::add_many()` to persist `expects_confirmation` into PostgreSQL.
  - Verified via test in `tests/test_delivery_service.py` that non-confirming providers record `expects_confirmation=False` and are never swept as expired dead-letters.

---

### TODO-1.3: Wire Worker Claim Semantics (`mark_dispatching` & `list_stuck`)
- **Status**: **`[x] COMPLETED`**
- **Severity**: Critical
- **Implementation Details**:
  - In `DeliveryService.deliver()`, `attempt.mark_dispatching(datetime.now(timezone.utc))` is committed to PostgreSQL before invoking `provider.send()`.
  - Added `stuck_lost_ttl_seconds` (300s) and `stuck_in_doubt_ttl_seconds` (300s) to `config/reconciliation.py`.
  - In `ReconciliationService.sweep_once()`, added `_requeue_stuck()` which queries `uow.delivery_attempts.list_stuck(...)`, calls `mark_requeued()`, and writes a new outbox entry to re-dispatch the job.
  - Verified via test `tests/test_reconciliation_service.py::test_reconciliation_requeues_stuck_attempts`.

---

## 4. Priority 2: Reliability & Architecture Fixes

### TODO-2.1: Prevent 24-Hour Dispatch Lockout on Worker Crash
- **Status**: **`[x] COMPLETED`**
- **Severity**: High
- **Implementation Details**:
  - Updated `jobqueue/dispatch_guard.py`:
    - `acquire(key)` sets short-lived in-flight lease with `ex=180` (3 minutes).
    - `complete(key)` marks the dispatch permanently finished with 24h key `pulse:dispatched:{key}`.
    - `release(key)` removes the in-flight key immediately upon recoverable failure.
  - Updated `DeliveryService.deliver()` to call `guard.complete()` on successful delivery and `guard.release()` on retryable failure.
  - If a worker crashes mid-send, the 180s lease expires automatically, unblocking stuck attempt sweeps and queue retries.

---

### TODO-2.2: Connect Database `providers` Table & API to Dispatch Runtime
- **Status**: **`[x] COMPLETED`**
- **Severity**: High
- **Affected Files**:
  - `database/models/provider.py`
  - `repositories/provider_repository.py`
  - `providers/registry.py`
  - `workers/context.py`
  - `services/delivery_service.py`
- **Problem**:
  Pulse has a `providers` table and `/api/v1/providers` CRUD API, but `workers/context.py` instantiates `ProviderRegistry` strictly against static `.env` settings. The DB table is completely bypassed, preventing per-tenant provider configurations.
- **Action Plan**:
  1. Update `ProviderResolver` protocol in `services/delivery_service.py` to accept `(tenant_id: TenantId, channel: ChannelType)`.
  2. In `ProviderRegistry` or worker resolution, check database for active tenant-specific provider rows:
     ```python
     stored = await uow.providers.get_active_by_channel(tenant_id, channel)
     ```
  3. Dynamically instantiate the provider driver using the JSONB `credentials`/`settings` config if present.
  4. If no tenant DB row exists, fall back to default `.env` provider instance.
  5. Pass `provider_id=stored.id` into `attempt.mark_sent(...)` so the foreign key is properly tracked.

---

### TODO-2.3: Upgrade Recipient Resolution Beyond In-Memory Stub
- **Status**: **`[x] COMPLETED`**
- **Severity**: High
- **Implementation Details**:
  - Implemented `DatabaseRecipientClient` in `clients/sentinel_client.py` adhering to `SentinelClient` protocol.
  - Parses inline recipients from `payload["recipients"]` if present.
  - If omitted, resolves the recipient from `uow.recipients.get_by_external_ref(tenant_id, ...)` using `recipient_id`, `user_id`, or `external_ref`.
  - Wired into `main.py` and `workers/scheduler_worker.py`.
  - Added unit test `tests/test_recipient_client.py` verifying database lookup and fallback behavior.

---

## 5. Priority 3: Functional Gaps & Usability

### TODO-3.1: Enforce Recipient Quiet Hours
- **Status**: **`[x] COMPLETED`**
- **Severity**: Medium
- **Implementation Details**:
  - Added `window_end(reference_dt: datetime) -> datetime` to `domain/value_objects/quiet_hours.py` handling normal and overnight (e.g. 22:00 - 07:00) windows.
  - In `NotificationService.fan_out()`: checks `preference.quiet_hours.covers(recipient_local_time)`.
  - If quiet hours apply, computes window end in UTC, stamps `attempt.next_retry_at`, and passes `defer_until` to the Outbox.
  - Updated `OutboxRelay` to calculate `defer_by_seconds` from `defer_until` and pass `_defer_by` to the Arq queue.
  - Added unit tests in `tests/test_quiet_hours.py` and `tests/test_notification_service.py::test_fan_out_defers_during_quiet_hours`.

---

### TODO-3.2: Add Notification & Event List/Search Endpoints
- **Status**: **`[x] COMPLETED`**
- **Severity**: Medium
- **Backend Implementation**:
  - Added `GET /api/v1/notifications` with query parameters `status`, `limit`, and `offset`. Sets `X-Total-Count` header and returns `list[NotificationOut]`.
  - Added `GET /api/v1/events` with pagination and `X-Total-Count` header.
  - Implemented `list_by_tenant()` on `NotificationRepository` and `EventRepository`.
- **Remaining Action Items**:
  - Update `ui/index.html` to add a "Recent Notifications" table view with status badges, pagination, and clickable rows that open detail views.

---

### TODO-3.3: Implement Dead-Letter Management API & UI Replay
- **Status**: **`[x] COMPLETED`**
- **Severity**: Medium
- **Backend Implementation**:
  - Created `api/v1/dead_letters.py`:
    - `GET /api/v1/dead-letters`: Lists dead-lettered attempts for tenant with pagination.
    - `POST /api/v1/dead-letters/{attempt_id}/retry`: Verifies dead status, clears guard lock, marks attempt `PENDING` with updated retry count, and publishes an outbox dispatch entry.
  - Registered router in `main.py`.
- **Remaining Action Items**:
  - Update `ui/index.html` to add a "Dead Letters" tab displaying failed deliveries, failure reasons, error details, and an inline "Retry" button.

---

### TODO-3.4: Auto-Provision Recipient on SSE Stream Connect
- **Status**: **`[x] COMPLETED`**
- **Severity**: Medium
- **Implementation Details**:
  - In `api/v1/sse.py`, when a client connects to `/api/v1/sse/stream/{recipient}`, if `get_by_external_ref` returns None, it auto-upserts a new `Recipient` row in the database.
  - Prevents spurious `404: Unknown recipient` errors when a user opens the web console before their first notification is created.

---

## 6. Priority 4: Code Hygiene & Protocol Integrity

### TODO-4.1: Clean Up Ruff Lint Errors & Warnings
- **Status**: **`[x] COMPLETED`**
- **Implementation**: Removed unused `UserId` from line 11 in `services/identity_service.py`. `ruff check .` passes cleanly.

---

### TODO-4.2: Resolve Pydantic Field Shadowing Warning in LoggingSettings
- **Status**: **`[x] COMPLETED`**
- **Implementation**: Changed `json: bool = True` to `json_format: bool = Field(default=True, alias="json")` in `config/logging.py`. Startup produces zero warnings.

---

### TODO-4.3: Synchronize `UnitOfWork` Protocol with Identity Repositories
- **Status**: **`[x] COMPLETED`**
- **Implementation**: Added repository attributes (`tenants`, `users`, `memberships`, `api_keys`, `sessions`) to `UnitOfWork` protocol in `services/ports/uow.py` and implemented corresponding fakes in `tests/fakes.py::InMemoryUnitOfWork`.

---

### TODO-4.4: Add Foreign Key Constraints to Identity Schema
- **Status**: **`[x] COMPLETED`**
- **Implementation**:
  - Added `ForeignKey("tenants.id", ondelete="CASCADE")` and `ForeignKey("users.id", ondelete="CASCADE")` to `tenant_memberships`, `api_keys`, and `sessions` in `database/models/identity.py`.
  - Created and applied migration `b8d4f6a2c913_phase5_identity_foreign_keys.py`. Linear migration chain intact.

---

### TODO-4.5: Add API Router Test Suite with FastAPI `TestClient`
- **Status**: **`[x] COMPLETED`**
- **Severity**: Low
- **Affected Files**:
  - `tests/api/test_auth.py` (new)
  - `tests/api/test_events.py` (new)
  - `tests/api/test_notifications.py` (new)
  - `tests/api/test_dead_letters.py` (new)
  - `tests/api/test_sse.py` (new)
- **Problem**:
  The existing 50 tests are all service and repository unit tests. There are no automated integration tests verifying HTTP routing, header parsing, session cookies, API key authentication dependencies, JSON serialization, or HTTP status codes.
- **Action Plan**:
  1. Add `pytest-asyncio` / `httpx.AsyncClient` test harness for FastAPI routes in `tests/api/conftest.py`.
  2. Implement tests for authentication routes (`/api/v1/auth/login`, `/api/v1/auth/me`).
  3. Implement tests for event ingestion (`POST /api/v1/events`).
  4. Implement tests for notification lookup and listing (`GET /api/v1/notifications`).
  5. Implement tests for dead-letter listing and replay (`POST /api/v1/dead-letters/{id}/retry`).

---

## 7. Priority 5: Strategic Market Parity Roadmap

Features needed to compete directly with **Knock** and **Novu**:

1. **Batching & Digest Engine**:
   - Add a `digest` workflow step with configurable time windows (e.g. `15m`, `1h`, `1d`) or thresholds (e.g. `10 items`).
   - Store events in a digest aggregation buffer; on window expiry, render a single consolidated summary email/push.
2. **Persistent In-App Notification Center**:
   - Add `in_app_messages` table storing notifications with `is_read`, `read_at`, `is_archived`.
   - Provide REST endpoints: `GET /inbox`, `POST /inbox/{id}/read`, `POST /inbox/read-all`.
   - Publish a lightweight headless JS/React component.
3. **Multi-Step Workflows (DAGs)**:
   - Expand `WorkflowDefinition` from flat channel lists to ordered steps with conditions and delays (e.g. `Step 1: In-App` $\rightarrow$ `Delay: 10m` $\rightarrow$ `If unread: Send Email`).
4. **Production Provider Drivers**:
   - Email: AWS SES (`boto3`), Resend, SendGrid.
   - SMS: Twilio, MessageBird / Sinch.
   - Push: APNs (HTTP/2 with `.p8` token credentials).
   - Chat: Slack webhook and bot driver.
5. **OpenTelemetry & Distributed Tracing**:
   - Inject trace contexts (`traceparent`) into Outbox entries and Arq payloads.
   - Connect HTTP ingestion $\rightarrow$ Outbox Relay $\rightarrow$ Worker Dispatch $\rightarrow$ Webhook callback into a single trace view.
