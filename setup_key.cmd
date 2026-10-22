@echo off
rem ============================================================
rem  Double-click runner for setup_key.py
rem  It finds a working Python, then asks you to paste your
rem  DeepSeek API Key (input is hidden), verifies it, and writes
rem  it into .env.
rem  Keep this file ASCII-only: cmd.exe reads .cmd in the OEM
rem  code page and non-ASCII bytes break batch parsing.
rem ============================================================
setlocal

set "PY="
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
if defined PY goto chosen

where python >nul 2>nul
if errorlevel 1 goto try_bundled
python -c "import sys" >nul 2>nul
if errorlevel 1 goto try_bundled
set "PY=python"
goto chosen

:try_bundled
set "BUNDLED=C:\Users\1\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED%" set "PY=%BUNDLED%"
if not defined PY goto nopython

:chosen
"%PY%" "%~dp0setup_key.py" %*
set "RC=%errorlevel%"
if /i "%~1"=="--help" exit /b %RC%
echo.
pause
exit /b %RC%

:nopython
echo [ERROR] No usable Python found. Open a new terminal and run: python --version
pause
exit /b 9009
