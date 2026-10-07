@echo off
rem Runs the Sentinel test console (Streamlit) without opening a browser tab; start_sentinel.bat opens
rem a new browser window once it is ready. Usage: devtools\run_console.bat [port]
title Sentinel console
cd /d "%~dp0.."
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8501"
".svenv\Scripts\streamlit.exe" run devtools\streamlit_app.py --server.headless true --server.port %PORT%
echo.
echo The console has stopped. Close this window, or run devtools\run_console.bat to start it again.
