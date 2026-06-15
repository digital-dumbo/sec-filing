# MVP PRD: k10fetcher Local CLI

## 1. Purpose

Build `k10fetcher`, a lightweight local command-line tool that fetches the latest SEC EDGAR `10-K` filing for one or more company tickers, converts the filing HTML to PDF, and records all work in a local SQLite database.

This MVP intentionally avoids service infrastructure. There is no FastAPI server, UI, Celery, Redis, broker, scheduler, or long-running daemon.

## 2. MVP Goals

- Provide a simple CLI for submitting one or more tickers.
- Use a local SQLite database for input rows, processing state, output records, company lookup metadata, and audit history.
- Respect SEC rate limits with a maximum of 10 SEC HTTP requests per second across the CLI process.
- Keep database transitions atomic and consistent.
- Produce a local PDF file for each successful filing.
- Store explicit failure reasons for invalid tickers, SEC errors, conversion errors, and filesystem errors.
- Use `uv` for environment, dependency, and command execution.
- Enforce code quality with `ruff`.
- Include focused automated tests.
- Emit structured logs in a readable JSON-lines format.

## 3. Non-Goals

- No Redis, Celery, web API, Gradio, background workers, daily scheduler, multi-machine coordination, full-text search, or archive-management UI.

## 4. User Workflow

```bash
uv run k10fetcher bootstrap
uv run k10fetcher fetch AAPL META GOOGL
uv run k10fetcher status
```

## 5. Runtime Architecture

`k10fetcher` runs as a single local process: parse inputs, initialize SQLite, insert one row per ticker, process rows sequentially, respect a shared 10 req/s limiter, update state atomically, print a result table, and exit.

## 6. SQLite Data Model

Default paths:

```
.k10fetcher/k10fetcher.db
.k10fetcher/data/active/{ticker}/10-K/{ticker}_10-K_{filing_date}.pdf
```

Tables: `company_directory`, `filing_requests`, `filing_processing_steps`, `filing_responses`.

## 7. Atomic Transaction Rules

- Request submission: insert `filing_requests` (PENDING) + `QUEUED` step in one transaction.
- Processing start: set PROCESSING, set `started_at`, insert step — one transaction.
- Success: upsert `filing_responses` (SUCCESS), insert OUTPUT_WRITTEN + COMPLETED steps, mark request COMPLETED — one transaction. DB must not mark COMPLETED until PDF exists on disk.
- Failure: upsert `filing_responses` (FAILED), set request FAILED + error_reason — one transaction.

## 8. SEC Fetch Flow

1. Resolve ticker via `company_directory`.
2. Check cache in `filing_responses` for existing SUCCESS + PDF file.
3. Fetch `https://data.sec.gov/submissions/CIK##########.json`.
4. Find latest `10-K`.
5. Download primary filing HTML from SEC Archives.
6. Convert HTML to PDF (temp file + atomic rename).
7. Persist output row and mark request completed.

## 9. SEC Rate Limiting

Process-wide limiter of 10 req/s. `rate_limiter.acquire()` called before every SEC HTTP request. No sleeping inside SQLite transactions.

## 10. Logging

JSON-lines to `.k10fetcher/logs/k10fetcher.log`. Each line includes: `timestamp`, `level`, `event`, `batch_id`, `request_id`, `ticker`, `step`, `duration_ms`, `error`.

## 11. Error Handling

Error categories: `INVALID_TICKER`, `COMPANY_DIRECTORY_EMPTY`, `SEC_HTTP_ERROR`, `SEC_TIMEOUT`, `NO_10K_FOUND`, `HTML_DOWNLOAD_FAILED`, `PDF_CONVERSION_FAILED`, `FILESYSTEM_ERROR`, `DATABASE_ERROR`.

## 12. CLI Commands

- `bootstrap [--force]` — fetch and store company ticker mappings.
- `fetch TICKER... [--db-path] [--data-dir] [--log-path] [--rate-limit] [--no-cache]`
- `status [--batch-id] [--limit]`
- `doctor`

## 13. Acceptance Criteria

- `uv sync` completes.
- `uv run k10fetcher bootstrap` loads company mappings.
- `uv run k10fetcher fetch AAPL META GOOGL` produces PDFs or cache hits for valid tickers, visible failures for invalid ones.
- `uv run pytest` passes.
- `uv run ruff check .` passes.
- `uv run ruff format --check .` passes.
- No Redis, Celery, broker, web server, or daemon required.