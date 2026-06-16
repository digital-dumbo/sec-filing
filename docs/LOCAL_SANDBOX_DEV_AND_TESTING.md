# Local Sandbox Development And End-To-End Testing Guide

## Purpose

This guide explains how to set up `k10fetcher` locally in a sandbox-style workflow, run the CLI end to end, and verify the generated logs, SQLite rows, and PDF output.

The project is local-first. It does not require Redis, Celery, a web server, a broker, a scheduler, or a daemon.

## Prerequisites

Required tools:

- macOS or Linux shell
- `uv`
- Python managed by `uv`
- Network access to SEC endpoints for live end-to-end testing

From the project folder:

```bash
cd /Users/manishhmundra/dev/company/sec-filing
```

## Runtime Layout

By default, runtime files are written inside the project:

```text
.k10fetcher/
  k10fetcher.db
  data/
    active/{ticker}/10-K/*.pdf
  logs/
    k10fetcher.log
```

You can override this per command:

```bash
--db-path /tmp/k10fetcher.db
--data-dir /tmp/k10fetcher-data
--log-path /tmp/k10fetcher.log
```

You can also override the default app directory:

```bash
export K10FETCHER_APP_DIR=/tmp/k10fetcher-sandbox
```

## Clean Local Sandbox Setup

Use this when you want a fresh local run inside the project.

```bash
cd /Users/manishhmundra/dev/company/sec-filing
rm -rf .k10fetcher
uv sync
uv run k10fetcher doctor
```

Expected `doctor` output should show project-local paths:

```text
Application directory: OK
    /Users/manishhmundra/dev/company/sec-filing/.k10fetcher
Database path: OK
    /Users/manishhmundra/dev/company/sec-filing/.k10fetcher/k10fetcher.db
```

## Developer Quality Checks

Run these before and after changes:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Or run the bundled check script:

```bash
scripts/check.sh
```

Expected result:

```text
All checks passed!
All project checks completed.
```


## Reset Or Truncate Local SQLite Data

Use these commands when you want to clear local sandbox data and rerun tests from a known state.

### Option 1: Full Runtime Reset

This removes the SQLite DB, PDFs, and logs. The next `doctor`, `bootstrap`, or `fetch` command will recreate runtime folders as needed.

```bash
rm -rf .k10fetcher
uv run k10fetcher doctor
uv run k10fetcher bootstrap --force
```

Use this when you want the cleanest local sandbox reset.

### Option 2: Truncate Workflow Tables Only

This keeps `company_directory` so you do not need to download SEC ticker mappings again. It clears request, processing, and response history.

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.execute('pragma foreign_keys=on'); con.execute('delete from filing_processing_steps'); con.execute('delete from filing_responses'); con.execute('delete from filing_requests'); con.commit(); con.close()"
```

Verify workflow tables are empty:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); print('requests', con.execute('select count(*) from filing_requests').fetchone()[0]); print('steps', con.execute('select count(*) from filing_processing_steps').fetchone()[0]); print('responses', con.execute('select count(*) from filing_responses').fetchone()[0]); print('company_directory', con.execute('select count(*) from company_directory').fetchone()[0])"
```

Expected result:

```text
requests 0
steps 0
responses 0
company_directory <non-zero if already bootstrapped>
```

### Option 3: Truncate Everything In SQLite But Keep The DB File

This clears all application tables, including `company_directory`. You must run `bootstrap --force` again before live fetches.

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.execute('pragma foreign_keys=on'); con.execute('delete from filing_processing_steps'); con.execute('delete from filing_responses'); con.execute('delete from filing_requests'); con.execute('delete from company_directory'); con.commit(); con.close()"
uv run k10fetcher bootstrap --force
```

Use this when you want to keep the DB file but rebuild all data from scratch.

## Bootstrap Company Directory

The `fetch` command depends on `company_directory`. Run bootstrap before live fetches:

```bash
uv run k10fetcher bootstrap --force
```

Expected output:

```text
[5/5] Company directory cache: OK
    Upserted <count> ticker mappings into SQLite.
Bootstrap completed.
```

Verify company directory count:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); print(con.execute('select count(*) from company_directory').fetchone()[0])"
```

Verify specific tickers:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); print(con.execute(\"select ticker,cik,company_name from company_directory where ticker in ('ORCL','GOOGL','AAPL') order by ticker\").fetchall())"
```

Expected sample:

```text
[('AAPL', '0000320193', 'Apple Inc.'), ('GOOGL', '0001652044', 'Alphabet Inc.'), ('ORCL', '0001341439', 'ORACLE CORP')]
```

## End-To-End Live Fetch Test

Run a small live fetch:

```bash
uv run k10fetcher fetch AAPL
```

Or test multiple tickers:

```bash
uv run k10fetcher fetch ORCL GOOGL
```

During processing, the console shows step progress. Long steps animate dots on the same line:

```text
AAPL | Fetch metadata.
AAPL | Fetch metadata..
AAPL | Fetch metadata...
AAPL | Fetch metadata done
AAPL | Download filing HTML done
AAPL | Convert PDF done
```

Expected final result table:

```text
Input | Status  | Destination / Error
AAPL  | SUCCESS | .k10fetcher/data/active/AAPL/10-K/AAPL_10-K_<date>.pdf
```

## Verify PDF Output

List generated PDFs:

```bash
find .k10fetcher/data -type f -name '*.pdf' -print -exec ls -lh {} \;
```

A successful fetch should create a file like:

```text
.k10fetcher/data/active/AAPL/10-K/AAPL_10-K_2025-10-31.pdf
```

## Verify Status From CLI

Show recent rows:

```bash
uv run k10fetcher status --limit 10
```

Filter by one batch:

```bash
uv run k10fetcher status --batch-id <batch_id>
```

The batch id is printed by `fetch`:

```text
Batch submitted: 79d4d2ac16274513a6621974b432136d
```

## Verify SQLite Data

### Request Rows

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.row_factory=sqlite3.Row; rows=con.execute('select id,batch_id,raw_input,normalized_ticker,status,error_reason,requested_at,started_at,completed_at from filing_requests order by id desc limit 10').fetchall(); [print(dict(row)) for row in rows]"
```

Successful request sample:

```json
{
  "id": 1,
  "batch_id": "79d4d2ac16274513a6621974b432136d",
  "raw_input": "AAPL",
  "normalized_ticker": "AAPL",
  "status": "COMPLETED",
  "error_reason": null,
  "requested_at": "2026-06-15 12:40:12",
  "started_at": "2026-06-15 12:40:12",
  "completed_at": "2026-06-15 12:40:44"
}
```

### Response Rows

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.row_factory=sqlite3.Row; rows=con.execute('select request_id,ticker,cik,company_name,form_type,filing_date,accession_number,source_url,status,pdf_path,error_reason,created_at from filing_responses order by id desc limit 10').fetchall(); [print(dict(row)) for row in rows]"
```

Success response sample:

```json
{
  "request_id": 1,
  "ticker": "AAPL",
  "cik": "0000320193",
  "company_name": "Apple Inc.",
  "form_type": "10-K",
  "filing_date": "2025-10-31",
  "accession_number": "0000320193-25-000079",
  "source_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
  "status": "SUCCESS",
  "pdf_path": ".k10fetcher/data/active/AAPL/10-K/AAPL_10-K_2025-10-31.pdf",
  "error_reason": null
}
```

### Processing Steps

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.row_factory=sqlite3.Row; rows=con.execute('select request_id,step,message,created_at from filing_processing_steps order by id desc limit 30').fetchall(); [print(dict(row)) for row in rows]"
```

Expected successful step sequence includes:

```text
QUEUED
PROCESSING_STARTED
CIK_RESOLVED
METADATA_FETCH_STARTED
METADATA_FETCHED
HTML_DOWNLOAD_STARTED
HTML_DOWNLOADED
PDF_CONVERSION_STARTED
PDF_CONVERTED
OUTPUT_WRITTEN
COMPLETED
```

### Joined Request And Response View

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.row_factory=sqlite3.Row; rows=con.execute('select r.id,r.batch_id,r.normalized_ticker,r.status as request_status,o.status as response_status,o.filing_date,o.pdf_path,o.error_reason from filing_requests r left join filing_responses o on o.request_id=r.id order by r.id desc limit 10').fetchall(); [print(dict(row)) for row in rows]"
```

## Verify JSON Logs

Tail the log file:

```bash
tail -n 20 .k10fetcher/logs/k10fetcher.log
```

Pretty-print recent logs:

```bash
uv run python -c "import json; from pathlib import Path; p=Path('.k10fetcher/logs/k10fetcher.log'); [print(json.dumps(json.loads(line), indent=2)) for line in p.read_text().splitlines()[-10:]]"
```

Expected success events include:

```text
request_processing_started
metadata_fetched
html_downloaded
pdf_converted
request_completed
```

Expected failure event:

```text
request_failed
```

Sample success log:

```json
{
  "batch_id": "79d4d2ac16274513a6621974b432136d",
  "request_id": 1,
  "ticker": "AAPL",
  "step": "COMPLETED",
  "duration_ms": 32362,
  "pdf_path": ".k10fetcher/data/active/AAPL/10-K/AAPL_10-K_2025-10-31.pdf",
  "event": "request_completed",
  "level": "info",
  "timestamp": "2026-06-15T12:40:44.803298Z"
}
```

## Negative Test: Invalid Ticker

Run:

```bash
uv run k10fetcher fetch INVALIDXYZ
```

Expected terminal result:

```text
INVALIDXYZ | FAILED | INVALID_TICKER: not found in SEC company directory
```

Verify DB failure row:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); con.row_factory=sqlite3.Row; row=con.execute(\"select r.normalized_ticker,r.status as request_status,o.status as response_status,o.error_reason from filing_requests r join filing_responses o on o.request_id=r.id where r.normalized_ticker='INVALIDXYZ' order by r.id desc limit 1\").fetchone(); print(dict(row))"
```

Expected:

```json
{
  "normalized_ticker": "INVALIDXYZ",
  "request_status": "FAILED",
  "response_status": "FAILED",
  "error_reason": "INVALID_TICKER: not found in SEC company directory"
}
```

## Cache-Hit Test

After a successful fetch, run the same ticker again:

```bash
uv run k10fetcher fetch AAPL
```

If the previous PDF still exists, the pipeline should reuse it and record `CACHE_HIT`.

Verify:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); rows=con.execute(\"select step,message from filing_processing_steps where request_id=(select max(id) from filing_requests where normalized_ticker='AAPL') order by id\").fetchall(); [print(row) for row in rows]"
```

Expected to include:

```text
CACHE_HIT
COMPLETED
```

## Fresh Temporary Sandbox Run

Use this when you do not want to touch project-local `.k10fetcher`:

```bash
SANDBOX=/private/tmp/k10fetcher-sandbox
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX"

uv run k10fetcher bootstrap --force \
  --db-path "$SANDBOX/k10fetcher.db" \
  --log-path "$SANDBOX/k10fetcher.log"

uv run k10fetcher fetch AAPL \
  --db-path "$SANDBOX/k10fetcher.db" \
  --data-dir "$SANDBOX/data" \
  --log-path "$SANDBOX/k10fetcher.log"

uv run k10fetcher status \
  --db-path "$SANDBOX/k10fetcher.db" \
  --log-path "$SANDBOX/k10fetcher.log"

find "$SANDBOX/data" -type f -name '*.pdf' -print
```

## Troubleshooting

### All valid tickers fail as `INVALID_TICKER`

Likely cause: `company_directory` is empty.

Check:

```bash
uv run python -c "import sqlite3; con=sqlite3.connect('.k10fetcher/k10fetcher.db'); print(con.execute('select count(*) from company_directory').fetchone()[0])"
```

Fix:

```bash
uv run k10fetcher bootstrap --force
```

### zsh suggests `.k10fetcher`

If zsh asks whether `k10fetcher` should be corrected to `.k10fetcher`, answer `n`. The command is:

```bash
uv run k10fetcher fetch AAPL
```

Disable zsh correction for the session:

```bash
unsetopt correct
```

### PDF conversion takes time

This is expected for large SEC filings. Watch the CLI progress line:

```text
AAPL | Convert PDF.
AAPL | Convert PDF..
AAPL | Convert PDF...
```

### Live SEC calls fail

Check network access and retry. The automated tests do not require live SEC access:

```bash
uv run pytest
```

## Final Acceptance Checklist

Use this checklist before handing off a local sandbox build:

- `uv sync` completes.
- `uv run pytest` passes.
- `uv run ruff check .` passes.
- `uv run ruff format --check .` passes.
- `uv run k10fetcher doctor` shows project-local `.k10fetcher` paths.
- `uv run k10fetcher bootstrap --force` loads company mappings.
- `uv run k10fetcher fetch AAPL` creates a successful response.
- A PDF exists under `.k10fetcher/data/active/AAPL/10-K/`.
- `uv run k10fetcher status` shows `COMPLETED` and `SUCCESS`.
- `.k10fetcher/logs/k10fetcher.log` contains `request_completed`.
- Invalid ticker test records `FAILED` with `INVALID_TICKER`.
