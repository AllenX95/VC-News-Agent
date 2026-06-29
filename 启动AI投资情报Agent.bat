@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT="
for %%F in ("%~dp0*Agent.ps1") do set "SCRIPT=%%~fF"

if not defined SCRIPT (
    echo Cannot find the startup PowerShell script.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

endlocal
