# Observability (OpenTelemetry → OpenObserve)

Pulse records metrics with the OpenTelemetry SDK and exports them over OTLP/HTTP, so any OTLP backend works.
**OpenObserve** is the reference backend (metrics, dashboards, alerts, and later traces and logs in one place).
Export is off by default; with it off every instrument is a no-op.

## Turn it on

```bash
docker compose --profile observability up -d openobserve   # console: http://localhost:5080
```

```env
PULSE_OTEL__ENABLED=true
PULSE_OTEL__ENDPOINT=http://localhost:5080/api/default      # OTLP base URL; /v1/metrics is appended
PULSE_OTEL__HEADERS=Authorization=Basic <base64 of email:password>
PULSE_OTEL__ENVIRONMENT=production
PULSE_OTEL__EXPORT_INTERVAL_SECONDS=15
PULSE_OTEL__GAUGE_REFRESH_SECONDS=15
```

Dev credentials come from `docker-compose.yml` (`PULSE_O2_EMAIL` / `PULSE_O2_PASSWORD`; the password must satisfy
OpenObserve's strength rules). Use your own for anything shared. For OpenObserve Cloud, use the endpoint and
`Authorization` header from its Data Sources → Custom → OTLP page.

Each process reports as its own service: `pulse-api`, `pulse-email-worker`, `pulse-push-worker`, `pulse-sms-worker`,
`pulse-scheduler`.

## Metrics

Event metrics are recorded by the process that does the work. **State gauges are exported only by `pulse-scheduler`**
so each series has one writer. Names appear in OpenObserve with dots as underscores.

| Metric | Type | Attributes | Meaning |
| :--- | :--- | :--- | :--- |
| `pulse.events.ingested` | counter | `tenant` | Events accepted by `POST /events` |
| `pulse.ratelimit.rejected` | counter | `reason` (`rate`/`quota`) | 429s |
| `pulse.deliveries` | counter | `channel`, `outcome` (`sent`/`retrying`/`dead`/`failed`), `provider` | Attempt outcomes |
| `pulse.dead_letters` | counter | `channel` | Attempts dead-lettered |
| `pulse.provider.calls` | counter | `provider`, `channel`, `outcome` | Provider send calls (`ok`/`provider_error`/`recipient_error`) |
| `pulse.provider.duration` | histogram (ms) | same | Provider latency |
| `pulse.http.server.duration` | histogram (ms) | `method`, `route`, `status_class` | API latency (route template, not raw path) |
| `pulse.outbox.pending` | gauge | | Outbox rows waiting for the relay |
| `pulse.outbox.oldest_age` | gauge (s) | | Relay lag: age of the oldest pending row |
| `pulse.attempts.open` | gauge | `channel`, `status` | Unsettled attempts |
| `pulse.attempts.stuck` | gauge | | Pending/retrying attempts nobody is working |
| `pulse.dead_letters.total` | gauge | | Rows in `dead_letters` |
| `pulse.queue.depth` | gauge | `queue` | Jobs waiting per worker queue |

## Suggested alerts (PromQL; OpenObserve → Alerts → Metrics)

| Alert | Query | Fire when |
| :--- | :--- | :--- |
| Relay is behind | `max(pulse_outbox_oldest_age)` | > 60 for 5 min |
| Work is stuck | `max(pulse_attempts_stuck)` | > 0 for 10 min (the sweep should clear it) |
| Queue backing up | `max by (queue) (pulse_queue_depth)` | > 1000 for 5 min |
| Dead letters rising | `increase(pulse_dead_letters_total[15m])` | > 10 |
| Provider failing | `sum by (provider) (rate(pulse_provider_calls{outcome="provider_error"}[5m])) / sum by (provider) (rate(pulse_provider_calls[5m]))` | > 0.2 |
| Provider slow | `histogram_quantile(0.95, sum by (provider, le) (rate(pulse_provider_duration_bucket[5m])))` | > 5000 |
| API errors | `sum(rate(pulse_http_server_duration_count{status_class="5xx"}[5m]))` | > 0.1 |
| Scheduler silent | `absent(pulse_outbox_pending)` | for 5 min (no gauges means the scheduler is down) |

### Alerts and dashboard as code

The eight rules above and an overview dashboard ship as JSON in `ops/openobserve/` (`alerts.json`, `dashboard.json`).
Apply them with:

```bash
PULSE_ALERT_WEBHOOK_URL=https://hooks.example.com/... make o2-apply     # or: uv run python -m scripts.apply_openobserve
```

The script is idempotent (re-run it after editing the JSON and rules/dashboards are updated in place), supports
`--dry-run`, `--destination NAME` (use an existing OpenObserve destination) and `--no-alerts` / `--no-dashboard`.
It reads `PULSE_O2_URL`, `PULSE_O2_ORG`, `PULSE_O2_EMAIL`, `PULSE_O2_PASSWORD` (defaults match the dev compose file).
Notes: OpenObserve rejects localhost/private destination URLs, so the webhook must be a public address (Slack, PagerDuty,
etc.); and a rule can only be created once its metric has reported at least once, so rules whose stream is still
empty are skipped with a message: re-run after Pulse has been running for a minute.

## Notes

- Gauges refresh every `GAUGE_REFRESH_SECONDS` from one cheap cross-tenant query plus `ZCARD` per queue.
  A failing refresh is logged and retried; the last values keep being reported.
- Counters reset when a process restarts; use `rate()`/`increase()`, which handle resets.
- `tenant` is a label only on `pulse.events.ingested`. Tenants are a bounded set; don't add per-recipient labels.

## Log correlation

Every log line written while Pulse ingests an event, delivers an attempt or applies a provider receipt carries
`tenant_id`, `notification_id` and `attempt_id` (JSON keys, or `[tenant=… notification=… attempt=…]` in text mode;
`PULSE_LOGGING__JSON=false` selects text). Filter on `notification_id` to follow one message from ingest through the
worker to its receipt. Code binds ids with `telemetry.log_context.bind(...)` / `update_current(...)`.
