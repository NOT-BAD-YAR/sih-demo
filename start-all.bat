@echo off
setlocal EnableExtensions
title Insider Threat UEBA - Full Stack Launcher
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

rem ============================================================
rem   Load launcher config from .env (KEY=VALUE, '#' comments).
rem   Missing keys fall back to the defaults below (all ON).
rem   To stop the auto seeder / auto simulation in real-time
rem   runs, set in .env:
rem     LAUNCHER_RUN_SEED=0
rem     LAUNCHER_RUN_LIVE_DEMO=0
rem     LAUNCHER_AGENT_MODE=none
rem ============================================================
set "LAUNCHER_START_DOCKER=1"
set "LAUNCHER_START_CONTAINERS=1"
set "LAUNCHER_RUN_MIGRATE=1"
set "LAUNCHER_RUN_SEED=1"
set "LAUNCHER_RUN_LIVE_DEMO=0"
set "LAUNCHER_START_BACKEND=1"
set "LAUNCHER_START_FRONTEND=1"
set "LAUNCHER_OPEN_BROWSER=1"
set "LAUNCHER_INSTALL_NODE_DEPS=1"
set "LAUNCHER_AGENT_MODE=none"

if exist "%ROOT%.env" (
  for /f "usebackq eol=# tokens=1,2 delims==" %%a in ("%ROOT%.env") do (
    if "%%a"=="LAUNCHER_START_DOCKER" set "LAUNCHER_START_DOCKER=%%b"
    if "%%a"=="LAUNCHER_START_CONTAINERS" set "LAUNCHER_START_CONTAINERS=%%b"
    if "%%a"=="LAUNCHER_RUN_MIGRATE" set "LAUNCHER_RUN_MIGRATE=%%b"
    if "%%a"=="LAUNCHER_RUN_SEED" set "LAUNCHER_RUN_SEED=%%b"
    if "%%a"=="LAUNCHER_RUN_LIVE_DEMO" set "LAUNCHER_RUN_LIVE_DEMO=%%b"
    if "%%a"=="LAUNCHER_START_BACKEND" set "LAUNCHER_START_BACKEND=%%b"
    if "%%a"=="LAUNCHER_START_FRONTEND" set "LAUNCHER_START_FRONTEND=%%b"
    if "%%a"=="LAUNCHER_OPEN_BROWSER" set "LAUNCHER_OPEN_BROWSER=%%b"
    if "%%a"=="LAUNCHER_INSTALL_NODE_DEPS" set "LAUNCHER_INSTALL_NODE_DEPS=%%b"
    if "%%a"=="LAUNCHER_AGENT_MODE" set "LAUNCHER_AGENT_MODE=%%b"
  )
)

echo ====================================================
echo    Insider Threat UEBA - Full Stack Launcher
echo    Docker + Kafka/Postgres + Backend + Frontend
echo ====================================================
echo    Config: seed=%LAUNCHER_RUN_SEED% live=%LAUNCHER_RUN_LIVE_DEMO% agent=%LAUNCHER_AGENT_MODE%
echo ====================================================
echo.

rem ---------- 0. Preflight: docker CLI ----------
where docker >nul 2>nul
if errorlevel 1 (
  echo [ERROR] docker not found on PATH. Install Docker Desktop first.
  pause
  exit /b 1
)

rem ---------- 1. Start Docker Engine if not already running ----------
if not "%LAUNCHER_START_DOCKER%"=="1" goto :skip_docker
docker info >nul 2>nul
if not errorlevel 1 (
  echo [1] Docker engine already running.
) else (
  echo [1] Docker engine is not running - starting Docker Desktop...
  tasklist /FI "IMAGENAME eq Docker Desktop.exe" 2>nul | findstr /I "Docker Desktop.exe" >nul
  if errorlevel 1 (
    set "DDEXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    if not exist "%DDEXE%" set "DDEXE=%LocalAppData%\Docker\Docker Desktop.exe"
    if exist "%DDEXE%" (
      start "" "%DDEXE%"
      echo      Launched Docker Desktop.
    ) else (
      echo [WARN] Docker Desktop.exe not found at the usual install paths.
      echo        Please start Docker Desktop manually, then run this script again.
      pause
      exit /b 1
    )
  )
  echo      Waiting for the Docker engine to come up - up to 5 min...
  for /L %%i in (1,1,60) do (
    docker info >nul 2>nul
    if not errorlevel 1 goto :docker_up
    timeout /t 5 /nobreak >nul
  )
  echo [ERROR] Docker engine did not become ready within 5 minutes.
  pause
  exit /b 1
)
:docker_up
echo [OK] Docker engine ready.
echo.
:skip_docker

rem ---------- 2. Start Kafka + Postgres containers ----------
if not "%LAUNCHER_START_CONTAINERS%"=="1" (
  echo [skip] LAUNCHER_START_CONTAINERS=0 - not starting containers.
  goto :skip_containers
)
echo [2] Starting Kafka and Postgres containers...
docker compose up -d postgres kafka
if errorlevel 1 (
  echo [ERROR] 'docker compose up' failed. Check docker-compose.yml / Docker Desktop.
  pause
  exit /b 1
)

echo      Waiting for both containers to be healthy - up to 4 min...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $d=(Get-Date).AddMinutes(4); while((Get-Date) -lt $d){ $rows = docker compose ps --format json 2>$null; if($rows){ $j = $rows | ConvertFrom-Json; $pg = $j | Where-Object { $_.Service -eq 'postgres' -and $_.Health -eq 'healthy' }; $kf = $j | Where-Object { $_.Service -eq 'kafka' -and $_.Health -eq 'healthy' }; if($pg -and $kf){ 'healthy'; exit 0 } }; Start-Sleep 5 }; exit 1"
if errorlevel 1 (
  echo [ERROR] Kafka/Postgres did not become healthy. Run 'docker compose logs postgres kafka' to diagnose.
  pause
  exit /b 1
)
echo [OK] Kafka + Postgres healthy.
echo.
:skip_containers

rem ---------- 3. Preflight: venv (only if a Python step is enabled) ----------
set "NEED_PY=0"
if "%LAUNCHER_RUN_MIGRATE%"=="1" set "NEED_PY=1"
if "%LAUNCHER_RUN_SEED%"=="1" set "NEED_PY=1"
if "%LAUNCHER_START_BACKEND%"=="1" set "NEED_PY=1"
if /i not "%LAUNCHER_AGENT_MODE%"=="none" set "NEED_PY=1"
if "%NEED_PY%"=="1" (
  if not exist "%PY%" (
    echo [ERROR] venv python not found: %PY%
    echo        Run:  python -m venv .venv  ^&^&  .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
  )
)

rem ---------- 4. Preflight: node (only if a frontend step is enabled) ----------
set "NEED_NODE=0"
if "%LAUNCHER_START_FRONTEND%"=="1" set "NEED_NODE=1"
if "%LAUNCHER_INSTALL_NODE_DEPS%"=="1" set "NEED_NODE=1"
if "%NEED_NODE%"=="1" (
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] npm not found on PATH. Install Node.js first.
    pause
    exit /b 1
  )
  if not exist "%ROOT%dashboard\node_modules" (
    if not "%LAUNCHER_INSTALL_NODE_DEPS%"=="1" (
      echo [ERROR] dashboard dependencies missing. Set LAUNCHER_INSTALL_NODE_DEPS=1
      echo        or run: cd dashboard ^&^& npm install
      pause
      exit /b 1
    )
    echo [WARN] dashboard dependencies not installed - installing now...
    pushd "%ROOT%dashboard"
    call npm install
    if errorlevel 1 (
      echo [ERROR] npm install failed.
      popd
      pause
      exit /b 1
    )
    popd
  )
)

rem ---------- 5. Apply DB migrations ----------
if not "%LAUNCHER_RUN_MIGRATE%"=="1" (
  echo [skip] LAUNCHER_RUN_MIGRATE=0 - not running migrations.
  goto :skip_migrate
)
echo [3] Applying database migrations...
"%PY%" -m alembic -c db\alembic.ini upgrade head
if errorlevel 1 (
  echo [ERROR] 'alembic upgrade head' failed.
  pause
  exit /b 1
)
echo [OK] Migrations applied.
echo.
:skip_migrate

rem ---------- 6. Seed demo data (idempotent - safe to re-run) ----------
if not "%LAUNCHER_RUN_SEED%"=="1" (
  echo [skip] LAUNCHER_RUN_SEED=0 - not seeding demo data.
  goto :skip_seed
)
echo [4] Seeding demo data...
"%PY%" -X utf8 scripts\seed_demo.py
if errorlevel 1 (
  echo [WARN] demo seed did not complete - continuing anyway.
)
echo.
:skip_seed

rem ---------- 7. Start backend API ----------
if not "%LAUNCHER_START_BACKEND%"=="1" (
  echo [skip] LAUNCHER_START_BACKEND=0 - not starting the API.
  goto :skip_backend
)
echo [5] Starting backend API on http://localhost:8000 ...
start "UEBA API" cmd /k ""%PY%" -X utf8 -m uvicorn api.main:app --port 8000"

echo      Waiting for the API to come up - up to 2 min...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $d=(Get-Date).AddMinutes(2); while((Get-Date) -lt $d){ try { $r=Invoke-WebRequest -Uri http://localhost:8000/health -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){ 'up'; exit 0 } } catch {}; Start-Sleep 3 }; exit 1"
if errorlevel 1 (
  echo [ERROR] API did not answer on :8000. Check the "UEBA API" window.
  pause
  exit /b 1
)
echo [OK] Backend API ready.
echo.
:skip_backend

rem ---------- 8. Live demo injection (needs the seeded baseline) ----------
if not "%LAUNCHER_RUN_LIVE_DEMO%"=="1" goto :skip_livedemo
if not "%LAUNCHER_RUN_SEED%"=="1" (
  echo [WARN] LAUNCHER_RUN_LIVE_DEMO=1 but LAUNCHER_RUN_SEED=0 - live demo needs
  echo        the seeded baseline, so it is being skipped.
  goto :skip_livedemo
)
echo [5b] Injecting a live volume-spike demo alert...
"%PY%" -X utf8 scripts\inject_live.py
echo.
:skip_livedemo

rem ---------- 9. Start frontend dashboard ----------
if not "%LAUNCHER_START_FRONTEND%"=="1" (
  echo [skip] LAUNCHER_START_FRONTEND=0 - not starting the frontend.
  goto :skip_frontend
)
echo [6] Starting frontend on http://localhost:5173 ...
start "UEBA Dashboard" cmd /k "cd /d ""%ROOT%dashboard"" && npm run dev"

echo      Waiting for the frontend to come up - up to 2 min...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; $d=(Get-Date).AddMinutes(2); while((Get-Date) -lt $d){ try { $r=Invoke-WebRequest -Uri http://localhost:5173 -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200){ 'up'; exit 0 } } catch {}; Start-Sleep 3 }; exit 1"
if errorlevel 1 (
  echo [ERROR] Frontend did not answer on :5173. Check the "UEBA Dashboard" window.
  pause
  exit /b 1
)
echo [OK] Frontend ready.
echo.
:skip_frontend

rem ---------- 10. Windows agent (optional) ----------
if /i "%LAUNCHER_AGENT_MODE%"=="none" (
  echo [skip] LAUNCHER_AGENT_MODE=none - not running the agent.
  goto :skip_agent
)
if /i "%LAUNCHER_AGENT_MODE%"=="dryrun" (
  echo [7] Windows agent dry-run - one poll cycle, no Kafka...
  "%PY%" -X utf8 -m agents.windows_agent --once --dry-run
  goto :skip_agent
)
if /i "%LAUNCHER_AGENT_MODE%"=="live" (
  echo [7] Starting the Windows agent in its own window...
  start "UEBA Agent" cmd /k ""%PY%" -X utf8 -m agents.windows_agent"
  goto :skip_agent
)
echo [WARN] Unknown LAUNCHER_AGENT_MODE='%LAUNCHER_AGENT_MODE%' - expected none, dryrun or live.
:skip_agent

rem ---------- 11. Open the dashboard ----------
if "%LAUNCHER_OPEN_BROWSER%"=="1" (
  if "%LAUNCHER_START_FRONTEND%"=="1" start "" http://localhost:5173
)

echo ====================================================
echo    Launcher finished.
echo.
echo    Dashboard : http://localhost:5173
echo    API       : http://localhost:8000  (docs: /docs)
echo.
echo    Logins: analyst / analyst    admin / admin
echo.
echo    Backend and frontend run in their own windows
echo    (titled "UEBA API" and "UEBA Dashboard").
echo    Close those windows to stop them.
echo ====================================================
pause
endlocal