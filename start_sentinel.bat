@echo off
setlocal EnableExtensions
rem ============================================================================
rem  Sentinel local launcher (Windows)
rem    start_sentinel.bat          start the stack, the Kafka worker and the console
rem    start_sentinel.bat setup    also generate and load the synthetic data
rem                                (first run, or to reset all cases)
rem  Stop: close the "Sentinel worker" and "Sentinel console" windows, then
rem        run "docker compose down" if you also want to stop the containers.
rem ============================================================================

cd /d "%~dp0"
set "BIN=%~dp0.svenv\Scripts"
set "PORT=8501"
set "URL=http://localhost:%PORT%"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"

if not exist "%BIN%\sentinel.exe" (
    echo [x] Python environment not found at .svenv. Create it and run: pip install -r requirements.txt ^&^& pip install -e .
    goto :fail
)
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [i] Created .env from .env.example ^(full-stack settings^).
)

echo [1/6] Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo [x] Docker is not running. Start Docker Desktop and run this file again.
    goto :fail
)

echo [2/6] Checking AWS credentials for Bedrock...
aws sts get-caller-identity >nul 2>&1
if errorlevel 1 (
    echo [!] AWS credentials are missing or expired. Starting "aws login"...
    call aws login
    if errorlevel 1 goto :fail
)

echo [3/6] Starting containers: Postgres, Kafka, OPA, MCP servers...
docker compose up -d --build --wait
if errorlevel 1 goto :fail

if /i "%~1"=="setup" (
    echo [4/6] Generating and loading the synthetic dataset...
    "%BIN%\sentinel.exe" data generate
    if errorlevel 1 goto :fail
    "%BIN%\sentinel.exe" data load
    if errorlevel 1 goto :fail
    echo       Embedding the policy documents into the knowledge base...
    "%BIN%\sentinel.exe" data kb
    if errorlevel 1 goto :fail
    echo       Loading the customer network into Neo4j...
    "%BIN%\sentinel.exe" data graph
    if errorlevel 1 echo [!] Neo4j load failed: start Neo4j and check SENTINEL_NEO4J_* in .env, then run "sentinel data graph". Continuing with the in-memory graph.
) else (
    echo [4/6] Using the data already in Postgres ^(run "start_sentinel.bat setup" to reload it^).
)
"%BIN%\sentinel.exe" kafka init
if errorlevel 1 goto :fail

rem Each process gets its own window: Windows Terminal if present (start would add tabs to one window).
where wt >nul 2>&1
if errorlevel 1 (set "WT=") else (set "WT=1")

echo [5/6] Starting the Kafka worker in a new terminal window...
rem The windows run helper scripts by relative path: no nested quotes for wt/cmd to mangle.
if defined WT (
    wt -w new --title "Sentinel worker" -d "%CD%" cmd /k devtools\run_worker.bat
) else (
    start "Sentinel worker" /D "%CD%" cmd /k devtools\run_worker.bat
)

echo [6/6] Starting the test console...
curl -s -f -o nul "%URL%/_stcore/health" >nul 2>&1
if not errorlevel 1 (
    echo       The console is already running.
    goto :open
)
if defined WT (
    wt -w new --title "Sentinel console" -d "%CD%" cmd /k devtools\run_console.bat %PORT%
) else (
    start "Sentinel console" /D "%CD%" cmd /k devtools\run_console.bat %PORT%
)

set /a tries=0
:wait
curl -s -f -o nul "%URL%/_stcore/health" >nul 2>&1
if not errorlevel 1 goto :open
set /a tries+=1
if %tries% geq 90 (
    echo [x] The console did not start within 90 seconds. Check the "Sentinel console" window.
    goto :fail
)
ping -n 2 127.0.0.1 >nul
goto :wait

:open
if exist "%EDGE%" (
    start "" "%EDGE%" --new-window "%URL%"
) else (
    start "" "%URL%"
)
echo.
echo Sentinel is running.
echo   Console   %URL%   ^(choose "Kafka" or "API" mode in the sidebar^)
echo   API       http://localhost:8000/docs
echo   Airflow   http://localhost:8088
echo   Kafka UI  http://localhost:8080
echo   Worker    "Sentinel worker" window
exit /b 0

:fail
echo.
echo Sentinel did not start. Fix the problem above and run this file again.
pause
exit /b 1
