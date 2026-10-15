@echo off
setlocal
chcp 65001 >nul
rem Setto Claude: chat window for Claude Code with your company Claude account.
rem   Double-click. First run installs what is missing (Python, Claude Code, libraries), then makes a
rem   Start menu + desktop shortcut. Later starts update themselves from GitHub.
rem   Optional: "Setto Claude.bat" "<project folder>" to open in that folder.
rem No goto/labels on purpose: cmd mis-finds labels in LF files.
set "PROJ=%~1"
set "BASE=%LOCALAPPDATA%\Setto\claude"
set "VENV=%BASE%\venv"
set "DEPS=claude-agent-sdk==0.2.160 pywebview==6.2.1"

rem 1) Python (per-user, no admin) when there is none
set "PY="
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe") do (
  if not defined PY if exist %%P set "PY=%%~P"
)
if not defined PY (
  where py >nul 2>nul && for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not defined PY (
  where python >nul 2>nul && for /f "delims=" %%i in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
)
if not exist "%VENV%\Scripts\python.exe" if not defined PY (
  echo === Installing Python 3.12 one time only, needs internet ===
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile \"$env:TEMP\python-3.12.10-amd64.exe\""
  "%TEMP%\python-3.12.10-amd64.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=0 Include_test=0
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

rem 2) libraries (reinstalled when the pinned list changes)
set "READY="
if exist "%VENV%\ready.txt" findstr /x /c:"%DEPS%" "%VENV%\ready.txt" >nul 2>nul && set "READY=1"
if not defined READY (
  echo === Installing libraries, one time only ===
  if exist "%VENV%\ready.txt" del "%VENV%\ready.txt"
  if not exist "%VENV%\Scripts\python.exe" "%PY%" -m venv "%VENV%"
  "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q %DEPS%
  if not errorlevel 1 > "%VENV%\ready.txt" echo %DEPS%
)
if not exist "%VENV%\ready.txt" (
  echo Install failed. Take a screenshot of this window and send it to the person who gave you this program.
  pause
  exit /b 1
)

rem 3) Claude Code itself (official installer) when it is not on this PC
set "HASCLI="
if exist "%USERPROFILE%\.local\bin\claude.exe" set "HASCLI=1"
if not defined HASCLI where claude >nul 2>nul && set "HASCLI=1"
if not defined HASCLI (
  echo === Installing Claude Code, one time only ===
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://claude.ai/install.ps1 | iex"
)

rem 4) company profile setup, then the window (python.exe with a hidden console: no CMD flashes)
set "VPY=%VENV%\Scripts\python.exe"
call "%~dp0prepare.cmd" "%PROJ%"
if "%PROJ%"=="" (
  start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.py"
) else (
  start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.py" --cwd "%PROJ%"
)
