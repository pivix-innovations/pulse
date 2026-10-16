# ── Pulse ─────────────────────────────────────────────────────────────
# `make help` lists everything. Foreground targets (api, worker-*) are for
# development; `make start` runs the whole stack in the background with
# logs in ./logs and pidfiles in ./.run, and `make stop` tears it down.

SHELL := /bin/bash
UV    := uv run
PORT  ?= 9000

RUN_DIR := .run
LOG_DIR := logs

SERVICES := api email-worker push-worker sms-worker scheduler

.DEFAULT_GOAL := help

# ── Setup ─────────────────────────────────────────────────────────────

.PHONY: build
build: ## Install/refresh dependencies (uv sync)
	uv sync

.PHONY: migrate
migrate: ## Apply database migrations
	$(UV) alembic upgrade head

.PHONY: revision
revision: ## Create a new migration: make revision m="add foo"
	$(UV) alembic revision --autogenerate -m "$(m)"

.PHONY: o2-apply
o2-apply: ## Create/update the OpenObserve alert rules and dashboard (see docs/observability.md)
	$(UV) python -m scripts.apply_openobserve

.PHONY: seed-admin
seed-admin: ## Bootstrap the first platform admin + tenant (idempotent)
	$(UV) python -m scripts.seed_admin $(if $(email),$(email)) $(if $(password),$(password)) $(if $(tenant),$(tenant))

# ── Console (Next.js, web/) ───────────────────────────────────────────

.PHONY: web-install
web-install: ## Install console dependencies
	cd web && npm install

.PHONY: web
web: ## Run the Next.js console on :3100, proxying /api to the Pulse API on :$(PORT)
	cd web && PULSE_API_ORIGIN=http://localhost:$(PORT) npm run dev

.PHONY: web-build
web-build: ## Type-check, lint and production-build the console
	cd web && npx tsc --noEmit && npm run lint && npm run build

# ── Infrastructure (Postgres + Redis) ─────────────────────────────────

.PHONY: infra-up
infra-up: ## Start Postgres and Redis containers
	docker compose up -d

.PHONY: infra-down
infra-down: ## Stop Postgres and Redis containers
	docker compose down

.PHONY: infra-logs
infra-logs: ## Tail infrastructure logs
	docker compose logs -f

.PHONY: infra-nuke
infra-nuke: ## Stop infra AND delete its data volumes
	docker compose down -v

# ── Services (foreground, one terminal each) ──────────────────────────

.PHONY: api
api: ## Run the API server (foreground, auto-reload)
	$(UV) uvicorn main:app --reload --port $(PORT)

.PHONY: email-worker
email-worker: ## Run the email delivery worker (foreground)
	$(UV) arq workers.email_worker.WorkerSettings

.PHONY: push-worker
push-worker: ## Run the push delivery worker (foreground)
	$(UV) arq workers.push_worker.WorkerSettings

.PHONY: sms-worker
sms-worker: ## Run the SMS delivery worker (foreground)
	$(UV) arq workers.sms_worker.WorkerSettings

.PHONY: scheduler
scheduler: ## Run the scheduler (outbox relay, reconciliation, scheduled dispatch)
	$(UV) python -m workers.scheduler_worker

.PHONY: workers
workers: ## Run all workers in the background (no API)
	@$(MAKE) --no-print-directory start-email-worker start-push-worker start-sms-worker start-scheduler

# ── Full stack (background) ───────────────────────────────────────────

.PHONY: start
start: infra-up migrate ## Start everything: infra, migrations, API + all workers (background)
	@$(MAKE) --no-print-directory $(addprefix start-,$(SERVICES))
	@echo
	@echo "Pulse is up → http://localhost:$(PORT)/ui/  (logs in $(LOG_DIR)/, stop with 'make stop')"

.PHONY: stop
stop: ## Stop background API + workers (leaves infra running; use infra-down for that)
	@for svc in $(SERVICES); do \
		pidfile="$(RUN_DIR)/$$svc.pid"; \
		if [[ -f $$pidfile ]] && kill -0 "$$(cat $$pidfile)" 2>/dev/null; then \
			echo "stopping $$svc (pid $$(cat $$pidfile))"; \
			kill "$$(cat $$pidfile)"; \
		fi; \
		rm -f "$$pidfile"; \
	done

.PHONY: restart
restart: stop start ## Restart the background stack

.PHONY: down
down: stop infra-down ## Stop everything including infrastructure

.PHONY: status
status: ## Show what's running
	@for svc in $(SERVICES); do \
		pidfile="$(RUN_DIR)/$$svc.pid"; \
		if [[ -f $$pidfile ]] && kill -0 "$$(cat $$pidfile)" 2>/dev/null; then \
			echo "  $$svc: running (pid $$(cat $$pidfile))"; \
		else \
			echo "  $$svc: stopped"; \
		fi; \
	done
	@docker compose ps 2>/dev/null || true

.PHONY: logs
logs: ## Tail all service logs
	tail -f $(LOG_DIR)/*.log

# Generic background launcher: start-<service> runs CMD_<service>
# detached, with output in logs/ and a pidfile in .run/.
CMD_api          := $(UV) uvicorn main:app --port $(PORT)
CMD_email-worker := $(UV) arq workers.email_worker.WorkerSettings
CMD_push-worker  := $(UV) arq workers.push_worker.WorkerSettings
CMD_sms-worker   := $(UV) arq workers.sms_worker.WorkerSettings
CMD_scheduler    := $(UV) python -m workers.scheduler_worker

start-%:
	@mkdir -p $(RUN_DIR) $(LOG_DIR)
	@pidfile="$(RUN_DIR)/$*.pid"; \
	if [[ -f $$pidfile ]] && kill -0 "$$(cat $$pidfile)" 2>/dev/null; then \
		echo "$* already running (pid $$(cat $$pidfile))"; \
	else \
		nohup $(CMD_$*) > "$(LOG_DIR)/$*.log" 2>&1 & \
		echo $$! > $$pidfile; \
		echo "started $* (pid $$!, log $(LOG_DIR)/$*.log)"; \
	fi

# ── Quality ───────────────────────────────────────────────────────────

.PHONY: test
test: ## Run the test suite
	$(UV) pytest

.PHONY: lint
lint: ## Lint (ruff) and type-check (ty)
	$(UV) ruff check .
	$(UV) ty check

.PHONY: test-pg
test-pg: infra-up ## Repository contract tests against real Postgres (memory fakes + SQL, SKIP LOCKED)
	PULSE_PG_TESTS=1 $(UV) pytest tests/repositories -v

.PHONY: e2e
e2e: infra-up ## End-to-end smoke + chaos tests on real Postgres/Redis (set PULSE_REDIS__PORT if 6380 is taken)
	PULSE_E2E=1 $(UV) pytest tests/e2e -v

.PHONY: fmt
fmt: ## Format code (black + ruff --fix)
	$(UV) black .
	$(UV) ruff check --fix .

.PHONY: check
check: lint test ## Lint + tests

# ── Help ──────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
