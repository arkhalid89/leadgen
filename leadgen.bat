@echo off
setlocal enabledelayedexpansion
title Leadgen Manager
color 0A

set "REPO=%USERPROFILE%\Documents\Development\leadgen"
set "REPO_URL=https://github.com/arkhalidwork/leadgen.git"
set "API_PORT=8000"
set "WEB_PORT=3600"

:menu
cls
echo ===============================
echo        LEADGEN MANAGER
echo ===============================
echo.
echo   API  : http://localhost:%API_PORT%
echo   App  : http://localhost:%WEB_PORT%
echo.
call :status
echo.
echo 1. Run App          (stops any running instance first)
echo 2. Stop App
echo 3. Git Pull
echo 4. Install / Update Requirements
echo 5. Clone Repository
echo 6. Open App in Browser
echo 7. Exit
echo.
set "choice="
set /p choice=Enter your choice (1-7):

if "%choice%"=="1" goto runapp
if "%choice%"=="2" goto stopmenu
if "%choice%"=="3" goto gitpull
if "%choice%"=="4" goto install
if "%choice%"=="5" goto clonerepo
if "%choice%"=="6" goto openbrowser
if "%choice%"=="7" exit /b 0
goto menu


REM ---------------------------------------------------------------- helpers

REM Show whether each service is currently listening.
:status
call :portpid %API_PORT% APIPID
call :portpid %WEB_PORT% WEBPID
if defined APIPID (echo   [RUNNING] backend  on %API_PORT%  ^(pid !APIPID!^)) else (echo   [stopped] backend  on %API_PORT%)
if defined WEBPID (echo   [RUNNING] frontend on %WEB_PORT%  ^(pid !WEBPID!^)) else (echo   [stopped] frontend on %WEB_PORT%)
exit /b 0

REM Find the PID listening on a port. %1 = port, %2 = variable name to set.
:portpid
set "%~2="
for /f "tokens=5" %%a in ('netstat -ano -p TCP ^| findstr /r /c:":%~1 .*LISTENING"') do (
    if not "%%a"=="0" set "%~2=%%a"
)
exit /b 0

REM Kill whatever is listening on a port, including its child processes.
REM /T matters: the API spawns Chromium for scraping, and the frontend spawns
REM its own workers - killing only the parent would leave those behind.
:killport
call :portpid %~1 _PID
if defined _PID (
    echo   stopping %~2 on port %~1 ^(pid !_PID!^)...
    taskkill /F /T /PID !_PID! >nul 2>&1
    timeout /t 1 /nobreak >nul
) else (
    echo   nothing running on port %~1 ^(%~2^)
)
set "_PID="
exit /b 0

:checkrepo
if not exist "%REPO%\run.py" (
    echo.
    echo Repository not found at:
    echo   %REPO%
    echo Use option 5 to clone it first.
    echo.
    pause
    exit /b 1
)
exit /b 0


REM ---------------------------------------------------------------- stop

:stopmenu
cls
echo === Stopping LeadGen ===
echo.
call :stopall
echo.
echo Done.
pause
goto menu

REM Stop both services. Safe to call when nothing is running.
:stopall
call :killport %API_PORT% backend
call :killport %WEB_PORT% frontend
REM Sweep up any orphaned scraper browsers this app launched. Matching on the
REM ms-playwright path means only Playwright's own Chromium is targeted, so a
REM personal Chrome window is never touched. PowerShell rather than wmic, which
REM has been removed from recent Windows builds.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*ms-playwright*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
exit /b 0


REM ---------------------------------------------------------------- run

:runapp
call :checkrepo
if errorlevel 1 goto menu
cls
echo === Starting LeadGen ===
echo.
echo [1/4] Stopping any running instance...
call :stopall

cd /d "%REPO%"

echo.
echo [2/4] Checking the Python environment...
if not exist ".venv\Scripts\python.exe" (
    echo   No .venv found - creating one...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo   Could not create the virtual environment. Is Python on PATH?
        pause
        goto menu
    )
    echo   Installing Python packages...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    echo   Installing the Chromium build Playwright drives...
    ".venv\Scripts\python.exe" -m playwright install chromium
)

echo.
echo [3/4] Checking the frontend...
if not exist "web\node_modules" (
    echo   Installing npm packages ^(first run, this takes a few minutes^)...
    cmd /c "cd /d "%REPO%\web" && npm install"
)

echo.
echo [4/4] Launching...
start "LeadGen API" cmd /k "cd /d "%REPO%" && .venv\Scripts\python.exe run.py"
start "LeadGen Web" cmd /k "cd /d "%REPO%\web" && npm run dev"

echo.
echo   Waiting for the services to come up...
call :waitport %API_PORT% backend
call :waitport %WEB_PORT% frontend

echo.
call :status
echo.
echo   Opening http://localhost:%WEB_PORT%
start "" "http://localhost:%WEB_PORT%"
echo.
echo   Both services run in their own windows. Closing those windows, or
echo   choosing option 2 here, stops the app.
echo.
pause
goto menu

REM Poll a port for up to ~60s. %1 = port, %2 = label.
:waitport
set /a _tries=0
:waitloop
call :portpid %~1 _WPID
if defined _WPID (
    echo   %~2 is up.
    set "_WPID="
    exit /b 0
)
set /a _tries+=1
if !_tries! geq 60 (
    echo   %~2 did not start within 60s - check its window for errors.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto waitloop


REM ---------------------------------------------------------------- maintenance

:gitpull
call :checkrepo
if errorlevel 1 goto menu
cd /d "%REPO%"
cls
echo === Git Pull ===
echo.
echo Stopping the app first so files are not in use...
call :stopall
echo.
git pull
echo.
echo If dependencies changed, run option 4.
echo.
pause
goto menu


:install
call :checkrepo
if errorlevel 1 goto menu
cd /d "%REPO%"
cls
echo === Install / Update Requirements ===
echo.
call :stopall
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo Could not create the virtual environment. Is Python on PATH?
        pause
        goto menu
    )
)

echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Installing the Chromium build Playwright drives...
".venv\Scripts\python.exe" -m playwright install chromium

echo.
echo Installing frontend packages...
cmd /c "cd /d "%REPO%\web" && npm install"

echo.
echo All dependencies are up to date.
echo.
pause
goto menu


:clonerepo
cls
echo === Clone Repository ===
echo.
if exist "%REPO%\run.py" (
    echo The repository already exists at:
    echo   %REPO%
    echo.
    pause
    goto menu
)

if not exist "%USERPROFILE%\Documents\Development" (
    echo Creating the Development folder...
    mkdir "%USERPROFILE%\Documents\Development"
)

cd /d "%USERPROFILE%\Documents\Development"
echo Cloning...
git clone %REPO_URL% leadgen
echo.
if exist "%REPO%\run.py" (
    echo Clone complete. Run option 4 next to install dependencies.
) else (
    echo Clone failed - check the messages above.
)
echo.
pause
goto menu


:openbrowser
start "" "http://localhost:%WEB_PORT%"
goto menu
