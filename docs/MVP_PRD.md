# MVP PRD: k10fetcher Local CLI

## 1. Purpose

Build `k10fetcher`, a lightweight local command-line tool that fetches the latest SEC EDGAR `10-K` filing for one or more company tickers, converts the filing HTML to PDF, and records all work in a local SQLite database.

This MVP intentionally avoids service infrastructure. There is no FastAPI server, UI, Celery, Redis, broker, scheduler, or long-running daemon. The product should run from a terminal, complete the requested batch, print a clear result table, and exit.

## 2. MVP Goals

- Provide a simple CLI for submitting one or more tickers.
- Use a local SQLite database for input rows, processing state, output records, company lookup metadata, and audit history.
- Respect SEC rate limits with a maximum of 10 SEC HTTP requests per second across the CLI process.
- Keep database transitions atomic and consistent.
- Produce a local PDF file for each successful filing.
- Store explicit failure reasons for invalid tickers, SEC errors, conversion errors, and filesystem errors.
- Use `uv` for environment, dependency, and command execution.
- Enforce code quality with `ruff`.
- Include focused automated tests for CLI parsing, database transitions, rate limiting, SEC parsing, success paths, and failure paths.
- Emit structured logs in a readable JSON-lines format.

## 3. Non-Goals

- No Redis.
- No Celery.
- No web API.
- No Gradio or browser UI.
- No background workers after the CLI process exits.
- No daily scheduler or automated delta sync.
- No multi-machine coordination.
- No full-text search.
- No archive-management UI.

## 4. User Workflow

### Basic Command

```bash
uv run k10fetcher fetch AAPL
```

### Multiple Tickers

```bash
uv run k10fetcher fetch AAPL META GOOGL
```

### Comma-Separated Input

```bash
uv run k10fetcher fetch AAPL,META,GOOGL
```

### Bootstrap Company Directory

```bash
uv run k10fetcher bootstrap
```

### Check Previous Requests

```bash
uv run k10fetcher status
```

## 5. Runtime Architecture

`k10fetcher` runs as a single local process:

1. Parse CLI inputs.
2. Initialize SQLite schema if missing.
3. Ensure company directory exists, or instruct the user to run `bootstrap`.
4. Insert one input row per requested ticker.
5. Process rows in a bounded local execution flow.
6. Respect one shared SEC rate limiter capped at 10 requests per second.
7. For each row, update processing state and write output inside deliberate SQLite transactions.
8. Print a final terminal table and exit.

The MVP may process tickers sequentially for maximum simplicity and correctness. If concurrency is added later, it must remain bounded and must share the same global 10 requests-per-second limiter.

## 6. SQLite Data Model

The database file should default to:

```text
.k10fetcher/k10fetcher.db
```

PDF files should default to:

```text
.k10fetcher/data/active/{ticker}/10-K/{ticker}_10-K_{filing_date}.pdf
```

### `company_directory`

Stores SEC ticker-to-CIK mappings bootstrapped from SEC `company_tickers.json`.

```sql
CREATE TABLE IF NOT EXISTS company_directory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_company_directory_cik
ON company_directory (cik);
```

Important: `ticker` is unique, but `cik` is not unique. Some companies have multiple tickers for the same CIK.

### `filing_requests`

One row per user-submitted ticker.

```sql
CREATE TABLE IF NOT EXISTS filing_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    normalized_ticker TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')
    ),
    error_reason TEXT,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_filing_requests_batch
ON filing_requests (batch_id);

CREATE INDEX IF NOT EXISTS idx_filing_requests_status
ON filing_requests (status);
```

### `filing_processing_steps`

Append-only audit log for each meaningful step.

```sql
CREATE TABLE IF NOT EXISTS filing_processing_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    step TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES filing_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_processing_steps_request
ON filing_processing_steps (request_id);
```

Required steps:

- `QUEUED`
- `CIK_RESOLVED`
- `CACHE_HIT`
- `METADATA_FETCH_STARTED`
- `METADATA_FETCHED`
- `HTML_DOWNLOAD_STARTED`
- `HTML_DOWNLOADED`
- `PDF_CONVERSION_STARTED`
- `PDF_CONVERTED`
- `OUTPUT_WRITTEN`
- `COMPLETED`
- `FAILED`

### `filing_responses`

One row per completed or failed input.

```sql
CREATE TABLE IF NOT EXISTS filing_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL UNIQUE,
    ticker TEXT,
    cik TEXT,
    company_name TEXT,
    form_type TEXT NOT NULL DEFAULT '10-K',
    filing_date TEXT,
    accession_number TEXT,
    source_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    pdf_path TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES filing_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_filing_responses_ticker
ON filing_responses (ticker);
```

## 7. Atomic Transaction Rules

All state changes must be safe to inspect at any time.

### Input Submission Transaction

For each requested ticker:

1. Insert `filing_requests` row with `PENDING`.
2. Insert `filing_processing_steps` row with `QUEUED`.
3. Commit.

### Start Processing Transaction

Before network work:

1. Set input row to `PROCESSING`.
2. Set `started_at`.
3. Insert processing event.
4. Commit.

### Successful Processing Transaction

After the PDF has been fully written to disk:

1. Insert or replace the matching `filing_responses` row with `SUCCESS`.
2. Set `filing_requests.status = 'COMPLETED'`.
3. Set `completed_at`.
4. Insert `OUTPUT_WRITTEN`.
5. Insert `COMPLETED`.
6. Commit.

### Failed Processing Transaction

When any failure occurs:

1. Insert or replace the matching `filing_responses` row with `FAILED`.
2. Set `filing_requests.status = 'FAILED'`.
3. Set `error_reason`.
4. Set `completed_at`.
5. Insert `FAILED` event with the error message.
6. Commit.

### Filesystem Rule

The database must not mark a row as `COMPLETED` until the PDF file exists at the expected path.

Use a temporary file during conversion:

```text
{target_pdf}.tmp
```

Only rename it to the final PDF path after conversion succeeds.

## 8. SEC Fetch Flow

For each valid ticker:

1. Resolve ticker using `company_directory`.
2. Check cache in `filing_responses` for an existing `SUCCESS` row with an existing PDF file.
3. Fetch submissions JSON:

```text
https://data.sec.gov/submissions/CIK##########.json
```

4. Find the latest `10-K`.
5. Download primary filing HTML from SEC Archives.
6. Convert HTML to PDF.
7. Persist output row and mark input completed.

## 9. SEC Rate Limiting

The CLI must respect a global limit of 10 SEC HTTP requests per second.

MVP implementation requirement:

- Use a process-wide rate limiter around every SEC HTTP request.
- The limiter must apply to company directory bootstrap, submissions fetches, paginated submissions fetches, and HTML downloads.
- The limiter must not rely on sleeping inside database transactions.
- Database transactions should be short-lived and should not stay open while waiting for rate-limit capacity or while performing network calls.

Recommended simple approach:

- Use a monotonic-clock token bucket or sliding-window limiter.
- Before each SEC request, call `rate_limiter.acquire()`.
- If capacity is not available, sleep outside any SQLite transaction.

## 10. Logging

Write structured JSON-lines logs to:

```text
.k10fetcher/logs/k10fetcher.log
```

Each log line should include:

- `timestamp`
- `level`
- `event`
- `batch_id`
- `request_id`
- `ticker`
- `step`
- `duration_ms` when relevant
- `error` when relevant

Example:

```json
{"timestamp":"2026-06-15T10:00:00Z","level":"info","event":"metadata_fetched","batch_id":"...","request_id":1,"ticker":"AAPL","step":"METADATA_FETCHED","duration_ms":314}
```

## 11. Error Handling

The CLI must never silently drop a ticker.

Invalid or failed rows must appear in:

- terminal output,
- `filing_requests`,
- `filing_processing_steps`,
- `filing_responses`,
- structured logs.

Expected error categories:

- `INVALID_TICKER`
- `COMPANY_DIRECTORY_EMPTY`
- `SEC_HTTP_ERROR`
- `SEC_TIMEOUT`
- `NO_10K_FOUND`
- `HTML_DOWNLOAD_FAILED`
- `PDF_CONVERSION_FAILED`
- `FILESYSTEM_ERROR`
- `DATABASE_ERROR`

## 12. Terminal Output

At the end of each run, print a table:

```text
Batch: 7c62e4f1-...

| Input      | Status    | Destination / Error |
|------------|-----------|---------------------|
| AAPL       | SUCCESS   | .k10fetcher/data/active/AAPL/10-K/AAPL_10-K_2025-10-31.pdf |
| INVALIDXYZ | FAILED    | INVALID_TICKER: not found in SEC company directory |
| META       | SUCCESS   | .k10fetcher/data/active/META/10-K/META_10-K_2026-01-29.pdf |
```

## 13. CLI Commands

### `bootstrap`

Fetch and store company ticker mappings.

```bash
uv run k10fetcher bootstrap
```

Options:

- `--force`: refresh existing directory rows.

### `fetch`

Submit and process one batch.

```bash
uv run k10fetcher fetch AAPL META GOOGL
```

Options:

- `--db-path PATH`
- `--data-dir PATH`
- `--log-path PATH`
- `--rate-limit 10`
- `--no-cache`

### `status`

Show recent requests and outputs.

```bash
uv run k10fetcher status
```

Options:

- `--batch-id ID`
- `--limit N`

## 14. Tooling Requirements

Use `uv` for local setup:

```bash
uv sync
uv run k10fetcher --help
uv run pytest
uv run ruff check .
```

Required development commands:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## 15. Test Requirements

Automated tests must cover:

- CLI input parsing and ticker normalization.
- Comma-separated and space-separated ticker input.
- SQLite schema creation.
- Company directory upsert where one CIK has multiple tickers.
- Input row insertion with `PENDING`.
- Processing transition from `PENDING` to `PROCESSING`.
- Successful atomic transition to output `SUCCESS` and input `COMPLETED`.
- Failure atomic transition to output `FAILED` and input `FAILED`.
- Invalid ticker handling.
- Cache-hit behavior.
- SEC submissions parsing.
- Rate limiter behavior without real sleeping where possible.
- Logging output shape.
- Terminal result table formatting.

Network tests must be mocked. Tests must not call SEC live endpoints.

## 16. Acceptance Criteria

The MVP is complete when:

- A user can install dependencies with `uv sync`.
- A user can run `uv run k10fetcher bootstrap`.
- A user can run `uv run k10fetcher fetch AAPL META GOOGL`.
- Valid tickers produce local PDF outputs or cache hits.
- Invalid tickers produce visible terminal failures and database output rows.
- SQLite contains consistent input, processing event, and output records after every run.
- SEC requests are globally limited to 10 requests per second.
- `uv run pytest` passes.
- `uv run ruff check .` passes.
- `uv run ruff format --check .` passes.
- No Redis, Celery, broker, web server, or daemon is required.