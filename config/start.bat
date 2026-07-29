@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo uv was not found.
  echo Install uv from https://docs.astral.sh/uv/ and run this file again.
  echo.
  pause
  exit /b 1
)

cd /d "%SCRIPT_DIR%"
echo Preparing the OpenOntology local configuration center...
uv sync --locked
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Review the message above.
  pause
  exit /b 1
)

uv run --no-sync python -m app.main
if errorlevel 1 (
  echo.
  echo The configuration center stopped with an error.
  pause
  exit /b 1
)
