# UEBA dev task runner (Windows PowerShell).
# Usage from repo root:  .\scripts\dev.ps1 <task>
#   up          start infra (docker compose up -d)
#   down        stop infra (docker compose down)
#   logs        tail all service logs
#   ps          show service status + health
#   test        run the Phase-0 unit test suite
#   test-all    run full pytest suite
#   migrate     run alembic upgrade head (needs db phase)
#   seed        seed demo org + accounts (needs db/project phases)
#   env         copy .env.example -> .env (no overwrite)

param([string]$Task = "help")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

switch ($Task) {
  "up"   { docker compose up -d }
  "down" { docker compose down }
  "logs" { docker compose logs -f --tail=100 }
  "ps"   { docker compose ps }
  "test" { python -m pytest -m "unit or structure" -q }
  "test-all" { python -m pytest -q }
  "migrate" { python -m alembic -c db/alembic.ini upgrade head }
  "seed"  { python -m db.seed }
  "env"  { if (-not (Test-Path .env)) { Copy-Item .env.example .env; "Created .env" } else { "Warning: .env already exists - not overwritten" } }
  "help" {
    @"
UEBA dev tasks:
  up | down | logs | ps      Docker Compose controls
  test | test-all            Phase-0 unit suite / full suite
  migrate | seed             Phase 3+ DB tasks (once built)
  env                        create .env from example
"@
  }
  default { throw "Unknown task: $Task (try 'help')" }
}