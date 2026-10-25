@echo off
if "%1"=="dev" (
  uv run python scripts/serve.py --reload
) else (
  uv run python scripts/serve.py %*
)
