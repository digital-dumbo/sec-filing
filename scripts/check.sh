#!/usr/bin/env bash
set -euo pipefail

printf '[1/4] Ruff lint: running\n'
uv run ruff check .
printf '[2/4] Ruff format: checking\n'
uv run ruff format --check .
printf '[3/4] Tests: running\n'
uv run pytest
printf '[4/4] CLI doctor: checking local runtime\n'
uv run k10fetcher doctor
printf 'All project checks completed.\n'