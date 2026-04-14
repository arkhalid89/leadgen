@echo off
setlocal

REM LeadGen complete startup script (Windows)
REM Starts: Redis check -> Celery worker -> Flask app

set ROOT_DIR=%~dp0
cd /d "%ROOT_DIR%"

set VENV_PY=%ROOT_DIR%venv\Scripts\python.exe
set VENV_CELERY=%ROOT_DIR%venv\Scripts\celery.exe

if not exist "%VENV_PY%" (
  echo Python virtual environment not found: %VENV_PY%
  echo Create it first, then install requirements.
  exit /b 1
)

if not exist "%VENV_CELERY%" (
  echo Celery executable not found: %VENV_CELERY%
  echo Install dependencies: "%VENV_PY%" -m pip install -r requirements.txt
  exit /b 1
)

"%VENV_PY%" -c "import sys,redis; c=redis.Redis(host='localhost', port=6379, db=0); c.ping(); print('Redis OK')" >nul 2>&1
if errorlevel 1 (
  where redis-server >nul 2>&1
  if errorlevel 1 (
    echo Redis is not reachable at redis://localhost:6379 and redis-server is not installed.
    echo Install/start Redis, then re-run this script.
    exit /b 1
  )

  echo Starting Redis...
  start "LeadGen Redis" /min redis-server
  timeout /t 2 /nobreak >nul
)

"%VENV_PY%" -c "import sys,redis; c=redis.Redis(host='localhost', port=6379, db=0); c.ping()" >nul 2>&1
if errorlevel 1 (
  echo Redis failed health check after startup attempt.
  echo Please verify Redis is running on localhost:6379.
  exit /b 1
)

if "%CELERY_CONCURRENCY%"=="" set CELERY_CONCURRENCY=4

echo Starting Celery worker (concurrency=%CELERY_CONCURRENCY%)...
start "LeadGen Celery" cmd /k ""%VENV_CELERY%" -A task_queue.celery_app.celery_app worker --concurrency=%CELERY_CONCURRENCY% --loglevel=info"

timeout /t 1 /nobreak >nul

echo Starting Flask app...
"%VENV_PY%" app.py

endlocal
