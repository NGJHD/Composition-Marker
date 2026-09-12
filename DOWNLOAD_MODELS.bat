@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  Composition Marker - model downloader
rem
rem  This fetches the TWO large language models and nothing else. Everything
rem  else the application needs - the Python runtime, the inference binaries for
rem  every graphics vendor, and the 886 MB handwriting reader - already ships
rem  inside the release zip.
rem
rem  Run this once on a machine that has internet access, then copy the whole
rem  folder to the offline machine. The application itself never touches the
rem  network.
rem
rem  Needs curl.exe, included with Windows 10 1803 and later.
rem ---------------------------------------------------------------------------

echo.
echo   Composition Marker - downloading the language models
echo   ----------------------------------------------------
echo.
echo   Two files, about 24 GB in total. Everything else is already in
echo   this folder.
echo.
echo   This will take a while. Already-downloaded files are skipped, so it
echo   is safe to re-run this script if the connection drops.
echo.

if not exist "models" mkdir "models"

where curl.exe >nul 2>&1
if errorlevel 1 (
  echo   ERROR: curl.exe was not found.
  echo   It ships with Windows 10 version 1803 and later. On an older
  echo   version, download the two files listed in BUILD_NOTES.md by hand.
  echo.
  pause
  exit /b 1
)

set "FAILED="

rem  Both URLs are pinned to an exact revision -- a HuggingFace commit SHA,
rem  never a branch head. `resolve/main` would let an upstream re-upload
rem  silently hand a new machine different weights from the ones every
rem  measurement in BUILD_NOTES.md was taken against, and the size check would
rem  not catch it. To move to newer weights, change the SHA here deliberately
rem  and re-measure.
rem
rem  NOTE: a description must not contain ( or ) -- it is echoed inside a
rem  parenthesised if-block, and cmd would treat them as block delimiters.

set "QWEN=https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/4ca720788d1e01f1bff70c033e0d0028fd02e502"

rem  High quality comes from a different repository: it is a hybrid quant --
rem  IQ4_XS attention over IQ3_S feed-forward -- which is how it lands 3 GB
rem  under Q4_K_M and still fits a 16 GB card whole. BUILD_NOTES section 7.6.
set "IQ4XS=https://huggingface.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller/resolve/bcb1edfb517aa9ae2443bf22961862a3b7a4a5a6"

rem  Both ship: the app picks by video memory at startup and the dropdown lets
rem  the user override, so either may be selected on any machine.
call :get "models\Qwen3.8-27B-UD-IQ2_XXS.gguf" 7000000000 ^
  "%QWEN%/Qwen3.8-27B-UD-IQ2_XXS.gguf?download=true" ^
  "Language model, Low Quality - 7.3 GB"

call :get "models\Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller.gguf" 13000000000 ^
  "%IQ4XS%/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller.gguf?download=true" ^
  "Language model, High Quality - 13.5 GB, this is the long one"

rem  A fresh clone of the source repository has no models\ at all, because the
rem  whole folder is gitignored. In a release zip this file is already present
rem  and the check below simply skips.
if not exist "models\mmproj-F16.gguf" (
  echo.
  echo   models\mmproj-F16.gguf is missing. It normally ships inside the
  echo   release zip; fetching it now.
  call :get "models\mmproj-F16.gguf" 900000000 ^
    "%QWEN%/mmproj-F16.gguf?download=true" ^
    "Handwriting reader, the vision projector - 886 MB"
)

echo.
echo   ----------------------------------------------------
if defined FAILED (
  echo   SOME DOWNLOADS FAILED. Re-run this script; finished files are skipped.
) else (
  echo   Done. Start the application with run.bat.
  echo.
  echo   This folder can now be copied to a computer with no internet
  echo   connection at all.
)
echo.
pause
exit /b 0

rem ---------------------------------------------------------------------------
:get
rem  %1 target  %2 minimum bytes  %3 url  %4 description
set "TARGET=%~1"
set "MINSIZE=%~2"
set "URL=%~3"
set "DESC=%~4"

if exist "%TARGET%" (
  for %%A in ("%TARGET%") do set "SIZE=%%~zA"
  if !SIZE! GEQ %MINSIZE% (
    echo   [skip] %DESC% - already present
    exit /b 0
  )
  echo   [redo] %DESC% - previous file was incomplete
  del /Q "%TARGET%"
)

echo   [....] %DESC%
rem  -C - resumes a partial download rather than starting over.
curl.exe -L --fail --retry 5 --retry-delay 5 -C - -# -o "%TARGET%" "%URL%"
if errorlevel 1 (
  echo   [FAIL] %DESC%
  set "FAILED=1"
  exit /b 1
)

for %%A in ("%TARGET%") do set "SIZE=%%~zA"
if !SIZE! LSS %MINSIZE% (
  echo   [FAIL] %DESC% - file is smaller than expected ^(!SIZE! bytes^)
  set "FAILED=1"
  exit /b 1
)
echo   [ ok ] %DESC%
exit /b 0
