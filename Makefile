# UEBA dev shortcuts (portable). On Windows prefer scripts/dev.ps1.
.PHONY: up down logs ps test test-all migrate seed help env

up:     ## start infra containers
	docker compose up -d

down:   ## stop infra containers
	docker compose down

logs:   ## tail logs
	docker compose logs -f --tail=100

ps:     ## service status
	docker compose ps

test:   ## Phase-0 unit suite
	python -m pytest -m "unit or structure" -q

test-all: ## full suite
	python -m pytest -q

migrate: ## alembic upgrade head (Phase 3+)
	python -m alembic -c db/alembic.ini upgrade head

seed:   ## seed demo org + accounts (Phase 3+)
	python -m db.seed

env:    ## create .env from example (no overwrite)
	@if not exist .env (copy .env.example .env) else (echo Warning: .env exists)

help: ## show tasks
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'