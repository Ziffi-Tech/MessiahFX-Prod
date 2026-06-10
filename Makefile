# ─────────────────────────────────────────────────────────────────────────────
# MeznaQuantFX AI — Makefile
# Requires: podman, podman-compose
# On Windows: use WSL2 or run podman-compose commands directly
# ─────────────────────────────────────────────────────────────────────────────

.DEFAULT_GOAL := help
COMPOSE := podman-compose
PROJECT := mezna-quantfx

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Infrastructure ────────────────────────────────────────────────────────────

.PHONY: infra-up
infra-up: ## Start infrastructure only (postgres + redis)
	$(COMPOSE) up -d postgres redis
	@echo "Waiting for postgres to be ready..."
	@$(COMPOSE) exec postgres pg_isready -U mezna -d mezna_trading || sleep 5

.PHONY: infra-down
infra-down: ## Stop infrastructure
	$(COMPOSE) stop postgres redis

# ── Database Migrations ───────────────────────────────────────────────────────

.PHONY: migrate
migrate: ## Run database migrations (alembic upgrade head)
	$(COMPOSE) run --rm migrate

.PHONY: migrate-status
migrate-status: ## Show current migration status
	$(COMPOSE) run --rm -e COMMAND="current" migrate

.PHONY: migrate-history
migrate-history: ## Show migration history
	$(COMPOSE) run --rm -e COMMAND="history" migrate

# ── Build ─────────────────────────────────────────────────────────────────────

.PHONY: build
build: ## Build all service images
	$(COMPOSE) build

.PHONY: build-no-cache
build-no-cache: ## Build all images without cache
	$(COMPOSE) build --no-cache

.PHONY: build-service
build-service: ## Build a single service: make build-service SERVICE=gateway
	$(COMPOSE) build $(SERVICE)

# ── Lifecycle ─────────────────────────────────────────────────────────────────

# Dev hot-reload: host code edits reflect in running containers instantly.
# Uses the docker-compose provider (honours .dockerignore + YAML merge). See
# docs/dev-hot-reload.md. On Windows prefer: pwsh scripts/dev-up.ps1
DEV_COMPOSE := podman compose -p mezna -f podman-compose.yml -f podman-compose.dev.yml

.PHONY: dev
dev: ## Start the stack with hot-reload (uvicorn --reload + next dev)
	$(DEV_COMPOSE) up -d

.PHONY: dev-down
dev-down: ## Stop the hot-reload stack
	$(DEV_COMPOSE) down

.PHONY: up
up: ## Start all services (detached)
	$(COMPOSE) up -d

.PHONY: down
down: ## Stop all services
	$(COMPOSE) down

.PHONY: restart
restart: ## Restart all services
	$(COMPOSE) restart

.PHONY: restart-service
restart-service: ## Restart a single service: make restart-service SERVICE=gateway
	$(COMPOSE) restart $(SERVICE)

.PHONY: stop
stop: ## Stop all services without removing containers
	$(COMPOSE) stop

# ── Logs ──────────────────────────────────────────────────────────────────────

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

.PHONY: logs-service
logs-service: ## Tail logs for a single service: make logs-service SERVICE=gateway
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

# ── Health Checks ─────────────────────────────────────────────────────────────

.PHONY: health
health: ## Check health of all services
	@echo "── Infrastructure ───────────────────────────────"
	@curl -sf http://localhost:5432 2>/dev/null && echo "postgres: checking via pg_isready" || true
	@$(COMPOSE) exec -T postgres pg_isready -U mezna -d mezna_trading && echo "postgres: READY" || echo "postgres: NOT READY"
	@curl -sf http://localhost:6379 2>/dev/null || $(COMPOSE) exec -T redis redis-cli ping && echo "redis: READY" || echo "redis: NOT READY"
	@echo "── Services ─────────────────────────────────────"
	@for svc in 8000 8001 8002 8003 8004 8005 8006 8007; do \
		name=$$(case $$svc in 8000) echo gateway;; 8001) echo market-data;; 8002) echo strategy;; 8003) echo risk;; 8004) echo executor;; 8005) echo ai-filter;; 8006) echo journal;; 8007) echo notifications;; esac); \
		curl -sf http://localhost:$$svc/health/live > /dev/null 2>&1 && echo "$$name ($$svc): OK" || echo "$$name ($$svc): UNREACHABLE"; \
	done
	@echo "── Dashboard ────────────────────────────────────"
	@curl -sf http://localhost:8501 > /dev/null 2>&1 && echo "dashboard (8501): OK" || echo "dashboard (8501): UNREACHABLE"

# ── Shell Access ──────────────────────────────────────────────────────────────

.PHONY: shell
shell: ## Open a shell in a service: make shell SERVICE=gateway
	$(COMPOSE) exec $(SERVICE) /bin/bash

.PHONY: db-shell
db-shell: ## Open psql shell
	$(COMPOSE) exec postgres psql -U mezna -d mezna_trading

.PHONY: redis-shell
redis-shell: ## Open redis-cli
	$(COMPOSE) exec redis redis-cli

# ── Risk Controls ─────────────────────────────────────────────────────────────

.PHONY: kill-switch
kill-switch: ## EMERGENCY: Activate kill switch — halts all trading immediately
	@echo "WARNING: This will halt all trading. Press Ctrl+C to cancel."
	@sleep 3
	curl -s -X POST http://localhost:8000/api/v1/control/kill \
		-H "Content-Type: application/json" \
		-d '{"reason": "Manual kill switch from Makefile"}' | python3 -m json.tool

.PHONY: kill-switch-status
kill-switch-status: ## Check kill switch status
	curl -s http://localhost:8000/api/v1/control/status | python3 -m json.tool

.PHONY: kill-switch-reset
kill-switch-reset: ## Reset kill switch (WARNING: re-enables trading)
	@echo "WARNING: This will re-enable trading. Confirm paper mode is set."
	@sleep 3
	curl -s -X POST http://localhost:8000/api/v1/control/reset \
		-H "Content-Type: application/json" | python3 -m json.tool

# ── Development Utilities ─────────────────────────────────────────────────────

.PHONY: dev-reset
dev-reset: ## Reset local dev environment (DESTROYS ALL DATA)
	@echo "WARNING: This destroys all local data including DB and Redis."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	$(COMPOSE) down -v
	$(COMPOSE) up -d postgres redis
	@sleep 5
	$(MAKE) migrate

.PHONY: lint
lint: ## Run ruff linter across all services and shared package
	ruff check shared/ services/ --fix

.PHONY: format
format: ## Format code with ruff
	ruff format shared/ services/

.PHONY: typecheck
typecheck: ## Run mypy type checking
	mypy shared/mezna_shared/ --ignore-missing-imports

.PHONY: ps
ps: ## Show running containers
	$(COMPOSE) ps
