@echo off
rem Code Receiver: submits codes decoded by Sammy Typer on another PC.
rem 1) Updates itself from the public repo (keeps the current files if offline).
rem 2) Starts as administrator in a console window, because Roblox may run as
rem    administrator and then ignores keys from a normal program.
rem Extra arguments are passed on, e.g. --other-clients or --dry-run.
rem Needs code_link.key copied from the decoder PC into this folder.
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
    py "%~dp0receiver_update.py"
    powershell -NoProfile -WindowStyle Hidden -Command "$extra = '%*'.Trim(); $launch = @('\"%~dp0code_receiver.py\"'); if ($extra) { $launch += $extra -split ' ' }; Start-Process -FilePath 'py' -ArgumentList $launch -Verb RunAs -WorkingDirectory '%~dp0'"
    exit /b
)
py "%~dp0code_receiver.py" %*
pause
