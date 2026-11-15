.PHONY: help install up down logs migrate revision api worker test test-unit lint format types boundaries secrets verify clean

BACKEND := backend
FRONTEND := frontend

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd $(BACKEND) && uv sync --extra dev
	cd $(FRONTEND) && pnpm install

up: ## Start the full stack
	docker compose up --build

down: ## Stop the stack and remove volumes
	docker compose down -v

logs: ## Tail service logs
	docker compose logs -f api worker

migrate: ## Apply database migrations
	cd $(BACKEND) && uv run alembic upgrade head

revision: ## Autogenerate a migration: make revision m="description"
	cd $(BACKEND) && uv run alembic revision --autogenerate -m "$(m)"

api: ## Run the API with reload
	cd $(BACKEND) && uv run uvicorn app.main:app --reload --port 8000

worker: ## Run the background worker
	cd $(BACKEND) && uv run arq app.workers.settings.WorkerSettings

test: ## Run the backend test suite
	cd $(BACKEND) && uv run pytest

test-unit: ## Run backend tests that need no Postgres or Redis
	cd $(BACKEND) && uv run pytest -m "not integration"

lint: ## Lint backend and frontend
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .

format: ## Auto-format the backend
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .

types: ## Type-check the backend
	cd $(BACKEND) && uv run mypy app

boundaries: ## Verify architecture import contracts
	cd $(BACKEND) && uv run lint-imports

secrets: ## Scan everything that could reach the repository for secrets
	@# Scans git history and staged content -- i.e. everything that can reach
	@# the remote. A working-tree scan is deliberately NOT run here: it flags a
	@# developer's own untracked .env, which is already blocked by .gitignore
	@# and by the pre-commit hook, and a gate that always fails trains people to
	@# ignore it. CI runs the working-tree scan on a clean checkout, where it is
	@# equivalent to scanning tracked files.
	@git rev-parse --verify HEAD >/dev/null 2>&1 \
		&& gitleaks git . --config .gitleaks.toml --redact --no-banner \
		|| echo "  (no commits yet -- history scan skipped)"
	gitleaks git . --staged --config .gitleaks.toml --redact --no-banner

verify: lint types boundaries secrets test ## The honest pre-claim gate — run before reporting progress
	@echo ""
	@echo "All backend gates passed. Frontend: cd $(FRONTEND) && pnpm lint && pnpm test && pnpm build"

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(BACKEND)/.pytest_cache $(FRONTEND)/dist
