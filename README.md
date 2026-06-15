# sec-filing

`sec-filing` is the new CLI-first MVP project for `k10fetcher`.

The product goal is intentionally small: a local Typer CLI that uses SQLite for input, processing events, and output records while fetching latest SEC 10-K filings. It avoids Redis, Celery, brokers, web APIs, and long-running daemons.

## Quick Start

    uv sync
    uv run k10fetcher --help
    uv run k10fetcher doctor
    uv run k10fetcher bootstrap
    uv run k10fetcher fetch AAPL META GOOGL
    uv run k10fetcher status

`uv sync` installs the Python dependencies declared in `pyproject.toml`. The CLI does not install SQLite separately; it uses Python's built-in `sqlite3` driver and initializes the local database file during `bootstrap`.

## Quality Checks

    uv run pytest
    uv run ruff check .
    uv run ruff format --check .

## Planning Documents

- `docs/TECHNICAL_DOCUMENTATION.md` covers architecture, diagrams, security notes, and technical flows.
- `docs/FUNCTIONAL_DOCUMENTATION.md` covers functional behavior, data models, and examples.
- `docs/LOCAL_SANDBOX_DEV_AND_TESTING.md` covers local setup, end-to-end testing, and DB/log verification.
- `docs/MVP_PRD.md` is the product requirement document.
- `docs/HARNESSING_STEPS.md` is the steering guide for incremental execution.
- `.agent/execplans/k10fetcher_mvp_execplan.md` is the living ExecPlan.

## Current CLI Behavior

The `bootstrap` command initializes SQLite and warms the `company_directory` cache from SEC `company_tickers.json`. The `fetch` command now processes each requested ticker end to end: ticker resolution, metadata fetch, HTML download, PDF conversion, and SQLite response persistence. The `status` command renders recent request and response state from SQLite.


## Runtime Outputs

By default, the CLI writes local state under the project-local `.k10fetcher` directory:

- SQLite database: `.k10fetcher/k10fetcher.db`
- PDFs: `.k10fetcher/data/active/{ticker}/10-K/`
- JSON-lines logs: `.k10fetcher/logs/k10fetcher.log`

Use `--db-path`, `--data-dir`, and `--log-path` to point a run at temporary or project-specific locations. Use `status --batch-id <id>` to inspect one batch.

Set `K10FETCHER_APP_DIR` to override the runtime directory.