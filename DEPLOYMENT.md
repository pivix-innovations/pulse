# Deploying Pulse (dev)

Pulse has three deployables:

1. **API**: the FastAPI app on **AWS Lambda** (Mangum) behind an API Gateway HTTP API, with a **Neon Postgres** database.
2. **Workers**: the arq delivery workers and the outbox scheduler on a **Linode** VM (systemd). They need a
   long-running process, so they cannot run on Lambda.
3. **Console**: the static Next.js console on **Cloudflare Pages**, with a Pages Function that proxies `/api/*` and
   `/health` to the API so the session cookie stays same-origin.

**Redis** (arq queue, rate limiter, circuit breaker) is a managed **Upstash** database, shared by the API and the workers.

| | |
|---|---|
| API | `https://dev-pulse-api.pivix.in` |
| Console | `https://dev-pulse.pivix.in` (Pages project `pulse-dev-ui`, `https://pulse-dev-ui.pages.dev`) |
| AWS account / region | `<aws-account-id>` / `ap-south-1` |
| CloudFormation stack | `pulse-dev` (Lambda `pulse-dev-api`) |
| Database | Neon project `<neon-project-id>`, branch `production` (separate from Quotient and Sentinel) |
| Workers | Linode `root@<worker-host-ip>` (Debian 13), code in `/opt/pulse`, config in `/etc/pulse/worker.env` |
| Email | Resend over HTTPS, from `no-reply@pulse-mailer.pivix.in` |

## How the pieces fit

`POST /api/v1/events` writes the notification and an **outbox** row in one database transaction (Lambda to Neon). The
scheduler on the Linode polls the outbox, publishes jobs to Redis, and the email, push and SMS workers deliver them.
An accepted event is never lost: if Redis, the Linode or the provider is down, the row waits and is relayed later.
Delivery is at-least-once and de-duplicated by job id.

## Configuration model

Settings use the `PULSE_` prefix with `__` for nesting (`PULSE_DATABASE__HOST`). Two additions were made for managed services:
`PULSE_DATABASE__SSL=true` (Neon) and `PULSE_REDIS__USERNAME` / `PULSE_REDIS__SSL=true` (Upstash).

[deploy/configure_env.py](deploy/configure_env.py) writes these into `.env`:

```sh
python deploy/configure_env.py "rediss://default:<token>@<host>.upstash.io:6379"
```

It reads `DATABASE_URL_UNPOOLED` from `.env` (written by `neon deploy`), splits it into the `PULSE_DATABASE__*` fields, and
generates `undread/pulse-secrets.env` once: the Fernet key that encrypts provider secrets at rest, the unsubscribe-link
signing key and the bootstrap admin. **Back that file up.** Losing the Fernet key makes stored provider secrets
unreadable. The Neon *unpooled* host is used on purpose (pgbouncer's transaction mode breaks asyncpg prepared statements).

`PULSE_SECURITY__INGRESS_API_KEYS` maps ingress keys to tenant UUIDs, as JSON, for example `{"pk_...": "<tenant uuid>"}`.

## API (Lambda)

### Prerequisites

[`uv`](https://docs.astral.sh/uv/) at `~/.local/bin/uv`, the project `.venv`, and AWS credentials plus a region:

```sh
export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=ap-south-1
```

The shared deploy bucket `quotient-dev-deploy-<account>-ap-south-1` must exist (created by Quotient's deploy script).

### Database and first admin

```sh
.venv/bin/alembic upgrade head                 # from your machine, against Neon
set -a; . undread/pulse-secrets.env; set +a    # PULSE_BOOTSTRAP_EMAIL / PASSWORD / TENANT
.venv/bin/python -m scripts.seed_admin         # idempotent; creates the admin and the pivix-dev tenant
```

Without the `PULSE_BOOTSTRAP_*` variables, `seed_admin` falls back to a weak dev account. Do not do that against Neon.
`undread/pulse-credentials.json` (git-ignored) records the login and the ingress key.

### Deploy

```sh
.venv/bin/python deploy/deploy.py              # build, upload, certificate, stack
.venv/bin/python deploy/deploy.py --no-domain  # API Gateway URL only
```

[deploy/template.yaml](deploy/template.yaml) creates the Lambda (`main.handler`), role, 7-day log group, HTTP API and
custom domain. The Lambda sets `PULSE_DATABASE__MAX_OVERFLOW=1` (Pulse requires a value above 0) with a pool of 2.
`firebase-admin` is left out of the Lambda build; FCM push runs on the workers.

Limits on Lambda: the SSE endpoint (`/api/v1/sse/stream`, used by the console's **Live** page) does not stream, because API
Gateway buffers the response.

### DNS (Porkbun)

| Type | Host | Value | Purpose |
|---|---|---|---|
| CNAME | `_a62de50b5bdaed1a3ca19272dc675ac2.dev-pulse-api` | `_4519f1d82b604fb556395a037d5f600d.wzccmgtwzk.acm-validations.aws` | ACM validation. **Keep it.** |
| CNAME | `dev-pulse-api` | `d-slz3k4mx79.execute-api.ap-south-1.amazonaws.com` | Domain to API Gateway |

### Verify

```sh
curl https://dev-pulse-api.pivix.in/health/    # {"status":"ok","database":true}
```

## Workers (Linode)

Four systemd services run as the unprivileged `pulse` user from `/opt/pulse`, reading `/etc/pulse/worker.env`
(mode 640, owner `root:pulse`):

| Unit | Command |
|---|---|
| `pulse-email` | `arq workers.email_worker.WorkerSettings` |
| `pulse-push` | `arq workers.push_worker.WorkerSettings` |
| `pulse-sms` | `arq workers.sms_worker.WorkerSettings` |
| `pulse-scheduler` | `python -m workers.scheduler_worker` (outbox relay, reconciliation, scheduled sends) |

All four restart on failure and start at boot.

### Email driver

Linode blocks outbound SMTP ports (25, 465, 587) on new accounts, so SMTP times out. The workers use the HTTPS Resend
driver instead:

```
PULSE_EMAIL__DRIVER=resend
PULSE_EMAIL__RESEND_API_KEY=<key>
PULSE_EMAIL__FROM_ADDRESS=no-reply@pulse-mailer.pivix.in
```

The key must be allowed to send from that domain. Per-tenant providers saved in the console (Resend, SendGrid, Twilio,
FCM, SES) take precedence over this env default.

### First-time setup

```sh
ssh root@<worker-host-ip>
apt-get update && apt-get install -y rsync curl ca-certificates
useradd -m -s /bin/bash pulse && mkdir -p /opt/pulse /etc/pulse && chown pulse:pulse /opt/pulse
su - pulse -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

Create `/etc/pulse/worker.env` from your `.env`: the `PULSE_*` variables except `PULSE_API__HOST/PORT` and the
`PULSE_EMAIL__*` ones, plus the three email lines above. Then `chown root:pulse` and `chmod 640` it. Install one unit
per row of the table, following this template (`$cmd` is the command in the table):

```ini
[Unit]
Description=Pulse <name> worker
After=network-online.target
Wants=network-online.target

[Service]
User=pulse
WorkingDirectory=/opt/pulse
EnvironmentFile=/etc/pulse/worker.env
ExecStart=/opt/pulse/.venv/bin/$cmd
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

### Releasing

```sh
rsync -az --exclude .venv --exclude .git --exclude web --exclude node_modules --exclude __pycache__ \
  --exclude undread --exclude .env --exclude logs --exclude tests --exclude docs \
  ./ root@<worker-host-ip>:/opt/pulse/
ssh root@<worker-host-ip> '
  chown -R pulse:pulse /opt/pulse
  su - pulse -c "cd /opt/pulse && ~/.local/bin/uv sync --frozen --no-dev"
  systemctl restart pulse-email pulse-push pulse-sms pulse-scheduler'
```

Run `alembic upgrade head` (from your machine) before restarting if the release has migrations.

### Operating

```sh
systemctl status pulse-email pulse-push pulse-sms pulse-scheduler
journalctl -u pulse-email -f
```

Healthy output: the scheduler logs `Outbox relay started`, and `arq` workers log the Redis version on start. A
`redis connection error ... AuthenticationError` means the Upstash credentials in `worker.env` are wrong (a password
shown as `********` in the Upstash dashboard is masked, not the real token).

## Console (Cloudflare Pages)

[web/](web) is built with `STATIC_EXPORT=1` (static files; the Docker build is unchanged). The Pages Function in
[web/functions/](web/functions) forwards `/api/*` and `/health` to `API_ORIGIN` (`https://dev-pulse-api.pivix.in`, set in
[web/wrangler.toml](web/wrangler.toml)). The session cookie is set by the API through the proxy, so it is host-only on the console domain.

```sh
wrangler login
cd web && ./deploy-cloudflare.sh
```

First-time setup only:

```sh
wrangler pages project create pulse-dev-ui --production-branch main
# attach the domain in the dashboard (Pages, Custom domains) or via the API:
# POST /accounts/<id>/pages/projects/pulse-dev-ui/domains {"name":"dev-pulse.pivix.in"}
```

DNS at Porkbun, with no proxy or redirect on the record:

| Type | Host | Value |
|---|---|---|
| CNAME | `dev-pulse` | `pulse-dev-ui.pages.dev` |

Sign in at `https://dev-pulse.pivix.in/login` with the admin from `undread/pulse-credentials.json`.

## Smoke test

Create a workflow, then send an event to a recipient carried in the payload (`delivered@resend.dev` is Resend's test inbox):

```sh
H="X-API-Key: <ingress key>"; B=https://dev-pulse-api.pivix.in/api/v1
curl -X POST $B/workflows -H "$H" -H 'content-type: application/json' -d '{"name":"smoke","event_type":"smoke.test","definition":{"channels":[{"channel":"email","subject":"Pulse smoke test","body":"Hello {{ name }}","format":"text"}]}}'
curl -X POST $B/events -H "$H" -H 'content-type: application/json' -d '{"event_type":"smoke.test","source":"manual","idempotency_key":"smoke-1","payload":{"name":"Ada","recipients":[{"external_ref":"u1","email":"delivered@resend.dev"}]}}'
curl $B/notifications/<id> -H "$H"     # attempts[0].status becomes "sent"
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Lambda init fails with `max_overflow` validation error | Pool overflow must be above 0 |
| Lambda: `No module named 'cryptography'` | Keep `cryptography` in `deploy/requirements-lambda.txt` |
| Email attempt `timed out after 30s` | SMTP is blocked on the Linode; use `PULSE_EMAIL__DRIVER=resend` |
| Email attempt stuck `retrying` after a worker restart | arq retries are disabled (app-level backoff); send a new event |
| `redis ... invalid username-password pair` | Wrong or masked Upstash token |
| `403 not authorized to send from <domain>` | Domain not verified for that Resend key |
| Console **Live** page never updates | SSE does not stream through API Gateway |
