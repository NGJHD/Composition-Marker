@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUNBUFFERED=1"
set "PATH=%~dp0bin;%PATH%"

if not exist "temp"   mkdir "temp"
if not exist "output" mkdir "output"

rem ---- preflight: name the missing file plainly, do not vanish -------------

set "MISSING="
if not exist "runtime\python.exe" set "MISSING=!MISSING! runtime\python.exe"

rem  One engine folder per backend. Only *some* must be present: an NVIDIA
rem  machine needs the cuda one, an AMD or Intel machine the vulkan or cpu one.
rem  The server picks at startup; here we only insist there is one to run.
set "HAVE_LLAMA="
for %%B in (cuda vulkan cpu) do if exist "bin\llama-%%B\llama-server.exe" set "HAVE_LLAMA=1"
if exist "bin\llama\llama-server.exe" set "HAVE_LLAMA=1"
if not defined HAVE_LLAMA set "MISSING=!MISSING! bin\llama-*\llama-server.exe"

if not "!MISSING!"=="" (
  echo.
  echo   Composition Marker cannot start.
  echo.
  echo   These files are missing from the application folder:
  for %%F in (!MISSING!) do echo       %%F
  echo.
  echo   If this is a fresh copy, run DOWNLOAD_MODELS.bat first - it fetches
  echo   the Python runtime, the models and the inference binaries.
  echo   Otherwise the folder may not have copied completely; copy it again.
  echo.
  pause
  exit /b 1
)

rem  The model weights are checked by the application rather than here, because
rem  config.json can point models_dir somewhere else entirely and this script
rem  cannot read it. The first screen names anything missing precisely.
set "WARN="
if not exist "models\Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller.gguf" set "WARN=1"
if not exist "models\Qwen3.8-27B-UD-IQ2_XXS.gguf" set "WARN=1"
if not exist "models\mmproj-F16.gguf"             set "WARN=1"
if defined WARN (
  echo.
  echo   Note: one or more model files are not in models\. If they have not
  echo   been moved elsewhere with paths.models_dir in config.json, run
  echo   DOWNLOAD_MODELS.bat. The application will say exactly which.
)

rem ---- find a free port ---------------------------------------------------

set "PORT="
for %%P in (8000 8001 8002 8003 8004 8005) do (
  if not defined PORT (
    netstat -ano -p tcp | findstr /r /c:"LISTENING" | findstr /c:":%%P " >nul 2>&1
    if errorlevel 1 set "PORT=%%P"
  )
)

if not defined PORT (
  echo.
  echo   Composition Marker cannot start: ports 8000-8005 are all in use.
  echo   Restart the computer and try again.
  echo.
  pause
  exit /b 1
)

echo.
echo   Composition Marker
echo.
echo   Keep this window open while you work.
echo   Closing it stops the application.
echo.

rem  A shortcut beside run.bat is the only reliable way to hand a
rem  non-technical user a clickable link: whether a console linkifies a URL
rem  depends on which terminal Windows happens to be using. Rewritten each
rem  launch so it always points at the live port.
set "SHORTCUT=%~dp0Open Composition Marker.url"
> "%SHORTCUT%" echo [InternetShortcut]
>>"%SHORTCUT%" echo URL=http://127.0.0.1:%PORT%

echo   [1/3] Program files found.
echo   [2/3] Port %PORT% is free.
echo   [3/3] Starting server; the browser opens by itself when it is ready.
echo.
echo   If it does not open, either double-click
echo   "Open Composition Marker" in this folder, or type this
echo   address into your browser:
echo.
echo       http://127.0.0.1:%PORT%
echo.

rem  Open the browser only once the server actually answers. Opening it first
rem  races the server and leaves the page retrying against a dead socket, which
rem  looks identical to a hang.
start "" /B "%~dp0runtime\python.exe" -m server.open_browser %PORT%

rem  --timeout-keep-alive covers the long idle gaps between SSE events while a
rem  page is being read.
"%~dp0runtime\python.exe" -m uvicorn server.main:app ^
  --host 127.0.0.1 ^
  --port %PORT% ^
  --timeout-keep-alive 300 ^
  --log-level warning

echo.
echo   Composition Marker has stopped.
pause
