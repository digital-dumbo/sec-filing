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

## Documentation Index

- [AGENTS.md](AGENTS.md) defines repository guidance for contributors and explains when to use ExecPlans.
- [docs/MVP_PRD.md](docs/MVP_PRD.md) captures the MVP product requirements, goals, non-goals, workflow, and acceptance criteria.
- [docs/FUNCTIONAL_DOCUMENTATION.md](docs/FUNCTIONAL_DOCUMENTATION.md) describes user-facing CLI behavior, data models, command examples, and expected outcomes.
- [docs/TECHNICAL_DOCUMENTATION.md](docs/TECHNICAL_DOCUMENTATION.md) covers architecture, components, runtime flows, diagrams, security notes, and testing considerations.
- [docs/LOCAL_SANDBOX_DEV_AND_TESTING.md](docs/LOCAL_SANDBOX_DEV_AND_TESTING.md) explains local setup, sandbox runs, end-to-end testing, and verification of SQLite, logs, and PDFs.
- [docs/HARNESSING_STEPS.md](docs/HARNESSING_STEPS.md) provides the milestone-by-milestone steering guide for building and validating the CLI MVP.
- [.agent/PLANS.md](.agent/PLANS.md) defines the required structure and maintenance rules for repository ExecPlans.
- [.agent/execplans/k10fetcher_mvp_execplan.md](.agent/execplans/k10fetcher_mvp_execplan.md) is the living execution plan for the `k10fetcher` MVP.

## Current CLI Behavior

The `bootstrap` command initializes SQLite and warms the `company_directory` cache from SEC `company_tickers.json`. The `fetch` command now processes each requested ticker end to end: ticker resolution, metadata fetch, HTML download, PDF conversion, and SQLite response persistence. The `status` command renders recent request and response state from SQLite.


## Runtime Outputs

By default, the CLI writes local state under the project-local `.k10fetcher` directory:

- SQLite database: `.k10fetcher/k10fetcher.db`
- PDFs: `.k10fetcher/data/active/{ticker}/10-K/`
- JSON-lines logs: `.k10fetcher/logs/k10fetcher.log`

Use `--db-path`, `--data-dir`, and `--log-path` to point a run at temporary or project-specific locations. Use `status --batch-id <id>` to inspect one batch.

Set `K10FETCHER_APP_DIR` to override the runtime directory.
