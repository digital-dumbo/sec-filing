# Build the `k10fetcher` CLI MVP

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This plan follows `.agent/PLANS.md`.

## Purpose / Big Picture

The goal is to deliver a minimal local product named `k10fetcher`. After this plan is complete, a user can run a Typer CLI to bootstrap SEC company metadata, submit one or more tickers, fetch the latest 10-K filing for each valid ticker, convert filing HTML to PDF, record every state transition in SQLite, and inspect results from the terminal. The system is intentionally local and simple: no Redis, no Celery, no broker, no API server, and no UI.

## Progress

- [x] (2026-06-15) Moved default runtime directory to project-local `.k10fetcher`.
- [x] (2026-06-15) Validated live SEC bootstrap and live AAPL 10-K PDF generation with temporary paths.
- [x] (2026-06-15) Implemented end-to-end fetch across ticker resolution, metadata, HTML download, PDF conversion, and response persistence.
- [x] (2026-06-15) Added filing HTML download and temp-file PDF conversion with atomic rename tests.
- [x] (2026-06-15) Added SEC submissions parsing, archive URL construction, and rate-limited metadata fetch tests.
- [x] (2026-06-15) Implemented SEC company directory bootstrap with cache skip/force behavior.
- [x] (2026-06-15) Implemented CLI submission and recent status table rendering.
- [x] (2026-06-15) Added operator-facing runtime/bootstrap console output and a `doctor` command.
- [x] (2026-06-15) Renamed core SQLite tables to `filing_requests`, `filing_processing_steps`, and `filing_responses`.
- [x] (2026-06-15) Created sibling project scaffold at `/Users/manishhmundra/dev/company/sec-filing`.
- [x] (2026-06-15) Completed final MVP acceptance and operator polish.

## Surprises & Discoveries

- Observation: The parent workspace only allowed direct writes inside `sec-company-filing-fetcher`, so creating the sibling `sec-filing` project required elevated filesystem permission.

## Decision Log

- Decision: Store default runtime files in the project-local `.k10fetcher` directory.
  Rationale: Keeping SQLite, PDFs, and logs beside the code makes local development easier.
  Date/Author: 2026-06-15 / Codex.

- Decision: Use Typer for the CLI boundary.
  Rationale: Typer provides typed commands, clean `--help`, and test support through `CliRunner`.
  Date/Author: 2026-06-15 / Codex.

- Decision: Keep the MVP sequential by default.
  Rationale: Sequential processing is easier to reason about and avoids SQLite write contention.
  Date/Author: 2026-06-15 / Codex.

## Outcomes & Retrospective

Final MVP acceptance is complete. The CLI can bootstrap SEC company metadata, fetch a live AAPL 10-K, generate a PDF, persist SQLite state, and emit JSON-lines logs without Redis, Celery, a web server, or a daemon.

## Context and Orientation

The repository root is `/Users/manishhmundra/dev/company/sec-filing`. The source package lives under `src/k10fetcher`. The CLI entry point is `src/k10fetcher/cli.py`, exposed as `k10fetcher` through `pyproject.toml`.

## Validation and Acceptance

    uv run pytest
    uv run ruff check .
    uv run ruff format --check .
    uv run k10fetcher bootstrap
    uv run k10fetcher fetch AAPL META GOOGL
    uv run k10fetcher status

## Interfaces and Dependencies

Use `typer`, `sqlite3`, `httpx`, `structlog`, `rich`, and `weasyprint`. Use `pytest`, `respx`, and `ruff` for validation.