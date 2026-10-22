@echo off
rem ============================================================
rem  One-click runner: picks a working Python automatically.
rem    run.cmd "hello"                          -> main.py "hello"
rem    run.cmd --mock "hello"                   -> offline demo
rem    run.cmd -m unittest discover -s tests -t . -v   -> tests
rem  Keep this file ASCII-only: cmd.exe reads .cmd in the OEM
rem  code page and non-ASCII bytes break batch parsing.
rem ============================================================
setlocal

set "PY="
rem Prefer a real Python under the user's Programs dir (any Python3*).
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
if /i "%~1"=="-m" goto direct
"%PY%" "%~dp0main.py" %*
exit /b %errorlevel%

:direct
"%PY%" %*
exit /b %errorlevel%

:nopython
echo [ERROR] No usable Python found. Open a new terminal and run: python --version
exit /b 9009
