@echo off
rem Company Claude profile setup (called by the launchers; %1 = project folder, may be empty).
rem The company login lives in %USERPROFILE%\.claude-company; memory, transcripts, skills and plugins
rem are shared with the personal profile when there is one (junctions); settings.json and the folder's
rem MCP servers are copied on every start. No goto/labels: cmd mis-finds labels in LF files.
set "PERSONAL=%USERPROFILE%\.claude"
set "CLAUDE_CONFIG_DIR=%USERPROFILE%\.claude-company"
if not exist "%CLAUDE_CONFIG_DIR%" mkdir "%CLAUDE_CONFIG_DIR%"
for %%D in (projects skills plugins) do (
  if exist "%PERSONAL%\%%D" if not exist "%CLAUDE_CONFIG_DIR%\%%D" mklink /J "%CLAUDE_CONFIG_DIR%\%%D" "%PERSONAL%\%%D" >nul
)
if exist "%PERSONAL%\settings.json" copy /y "%PERSONAL%\settings.json" "%CLAUDE_CONFIG_DIR%\settings.json" >nul
if not defined VPY set "VPY=python"
if not "%~1"=="" "%VPY%" "%~dp0sync_mcp.py" "%~1" >nul 2>nul
