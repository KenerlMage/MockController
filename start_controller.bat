@echo off
setlocal
chcp 65001 >nul
title MockController
cd /d "%~dp0"

echo ============================================
echo   MockController - config and package center
echo ============================================
echo.

set "HOST=0.0.0.0"
set "PORT=8070"
set "UI_URL=http://127.0.0.1:%PORT%/ui/"

rem ---------- 1/4 pick python ----------
rem order: local .venv -> sibling MockRunner venv -> python on PATH
set "PY="
if exist ".venv\Scripts\python.exe" (
  set "PY=%~dp0.venv\Scripts\python.exe"
  echo [1/4] python: local .venv
)
if not defined PY if exist "..\MockRunner\backend\.venv\Scripts\python.exe" (
  set "PY=%~dp0..\MockRunner\backend\.venv\Scripts\python.exe"
  echo [1/4] python: MockRunner venv
)
if not defined PY (
  set "PY=python"
  echo [1/4] python: from PATH
)

"%PY%" --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ or create .venv first.
  pause
  exit /b 1
)

rem ---------- 2/4 dependencies (install only when missing) ----------
"%PY%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo [2/4] fastapi / uvicorn missing, installing...
  "%PY%" -m pip install -r requirements.txt
  "%PY%" -c "import fastapi, uvicorn" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check network and run manually:
    echo         "%PY%" -m pip install -r requirements.txt
    pause
    exit /b 1
  )
) else (
  echo [2/4] dependencies OK
)

rem ---------- 3/4 port check ----------
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul 2>&1
if not errorlevel 1 (
  echo [ERROR] Port %PORT% is already in use. Close it first:
  echo         netstat -ano ^| findstr ":%PORT% "
  pause
  exit /b 1
)
echo [3/4] port %PORT% is free

rem ---------- 4/4 start service, open Edge when ready ----------
rem set ATE_NO_BROWSER=1 to skip opening the browser (automation)
if not "%ATE_NO_BROWSER%"=="1" (
  start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\open_ui.ps1" -Url "%UI_URL%"
  echo [4/4] Edge will open automatically: %UI_URL%
) else (
  echo [4/4] ATE_NO_BROWSER=1, browser skipped
)

echo.
echo   API      : http://127.0.0.1:%PORT%/api/v1/health
echo   Console  : %UI_URL%
echo   Data root: %~dp0repo
echo   Close this window to stop the service
echo.

"%PY%" -m uvicorn app:app --host %HOST% --port %PORT%
if errorlevel 1 (
  echo.
  echo [ERROR] Service exited with an error, see log above.
)
pause
