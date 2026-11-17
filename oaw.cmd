@echo off
setlocal DisableDelayedExpansion
set "OAW_ROOT=%~dp0"
if exist "%OAW_ROOT%payload\scripts\oaw.py" set "OAW_ROOT=%OAW_ROOT%payload\"
if exist "%OAW_ROOT%python\python.exe" (
    set "OAW_PYTHON=%OAW_ROOT%python\python.exe"
    goto run
)
if exist "%OAW_ROOT%backend\.venv\Scripts\python.exe" (
    set "OAW_PYTHON=%OAW_ROOT%backend\.venv\Scripts\python.exe"
    goto run
)
if exist "%OAW_ROOT%.open-agent-world\desktop-payload\python\python.exe" (
    set "OAW_PYTHON=%OAW_ROOT%.open-agent-world\desktop-payload\python\python.exe"
    goto run
)
where python >nul 2>nul
if not errorlevel 1 (
    set "OAW_PYTHON=python"
    goto run
)
echo OAW needs Python 3.11 or newer. Run scripts\setup.ps1 or use the desktop installation. 1>&2
exit /b 1
:run
"%OAW_PYTHON%" -I -B "%OAW_ROOT%scripts\oaw.py" %*
exit /b %errorlevel%
