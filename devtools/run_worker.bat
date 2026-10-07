@echo off
rem Runs the Sentinel Kafka worker (alerts + decisions). Started by start_sentinel.bat; can be run on its own.
title Sentinel worker
cd /d "%~dp0.."
".svenv\Scripts\sentinel.exe" worker all --concurrency 4
echo.
echo The worker has stopped. Close this window, or run devtools\run_worker.bat to start it again.
