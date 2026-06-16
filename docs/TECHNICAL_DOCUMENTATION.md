# Technical Documentation: k10fetcher

## Audience And Scope

This document is for developers, testers, product owners, scrum masters, architects, and security reviewers. It describes the local CLI architecture, component responsibilities, runtime flows, data boundaries, and test/security considerations for the `k10fetcher` MVP.

The product is intentionally local-first: no Redis, Celery, web server, broker, scheduler, or daemon. A CLI process starts, performs the requested work, writes SQLite/PDF/log artifacts, prints terminal output, and exits.

## Stakeholder View

| Role | Primary concern | Where to look |
| --- | --- | --- |
| Developer | Module boundaries, extension points, transaction rules | Components, module details, sequence diagrams |
| Tester | Observable behavior, mocked SEC calls, failure modes | Test strategy, flow diagrams, error handling |
| Product Owner | User workflow, status visibility, deliverable behavior | End-to-end flow, CLI commands, outputs |
| Scrum Master | Milestone tracking and acceptance | ExecPlan, validation commands, risks |
| Architect | Data flow, dependencies, rate limiting, persistence | Component diagram, sequence diagrams, storage model |
| Security | External calls, local files, logs, failure containment | Security section, data classification, controls |

## Runtime Defaults

The default runtime directory is project-local:

```text
/Users/manishhmundra/dev/company/sec-filing/.k10fetcher
```

Default artifacts:

```text
.k10fetcher/k10fetcher.db
.k10fetcher/data/active/{ticker}/10-K/{ticker}_10-K_{filing_date}.pdf
.k10fetcher/logs/k10fetcher.log
```

The runtime root can be overridden with `K10FETCHER_APP_DIR`. Individual CLI runs can override paths with `--db-path`, `--data-dir`, and `--log-path`.

## High-Level Component Diagram

```mermaid
flowchart LR
    User["Terminal User"] --> CLI["Typer CLI
src/k10fetcher/cli.py"]
    CLI --> Config["Settings
config.py"]
    CLI --> Pipeline["Fetch Pipeline
pipeline.py"]
    CLI --> Repo["Repository
repository.py"]
    Pipeline --> Repo
    Pipeline --> SEC["SEC Client
sec_client.py"]
    Pipeline --> PDF["PDF Converter
pdf.py"]
    Pipeline --> Logs["JSON Logs
logging.py"]
    SEC --> Limiter["Rate Limiter
rate_limit.py"]
    SEC --> SECWeb["SEC endpoints
company_tickers.json
submissions JSON
archive HTML"]
    Repo --> DB[("SQLite DB")]
    PDF --> Files["Local PDF files"]
    Logs --> LogFile["k10fetcher.log"]
```

## Component Responsibilities

| Component | File | Responsibility |
| --- | --- | --- |
| CLI | `src/k10fetcher/cli.py` | User commands, terminal tables, progress animation, CLI options |
| Config | `src/k10fetcher/config.py` | Runtime defaults, app directory, SEC User-Agent settings |
| DB | `src/k10fetcher/db.py` | SQLite connection, schema initialization, transaction helper |
| Repository | `src/k10fetcher/repository.py` | SQL access, row dataclasses, atomic state transitions |
| SEC Client | `src/k10fetcher/sec_client.py` | SEC HTTP calls, SEC payload parsing, archive URL construction |
| Rate Limiter | `src/k10fetcher/rate_limit.py` | Sliding-window SEC request limiter |
| PDF | `src/k10fetcher/pdf.py` | PDF path construction, WeasyPrint conversion, temp-file rename |
| Pipeline | `src/k10fetcher/pipeline.py` | End-to-end orchestration for one filing request |
| Logging | `src/k10fetcher/logging.py` | Structlog JSON-lines log configuration |

## External Dependencies

| Dependency | Use |
| --- | --- |
| `typer` | CLI command framework |
| `rich` | Terminal tables and readable output |
| `httpx` | SEC HTTP requests |
| `weasyprint` | HTML to PDF conversion |
| `structlog` | JSON-lines structured logs |
| `sqlite3` | Local database using Python standard library |
| `pytest`, `respx` | Automated tests and mocked HTTP |
| `ruff` | Linting and formatting |

## High-Level End-To-End Sequence

```mermaid
sequenceDiagram
    actor User
    participant CLI as Typer CLI
    participant Repo as Repository
    participant Pipe as Pipeline
    participant SEC as SEC Client
    participant Limit as Rate Limiter
    participant PDF as PDF Converter
    participant DB as SQLite
    participant Logs as JSON Log
    participant FS as File System

    User->>CLI: uv run k10fetcher fetch AAPL
    CLI->>DB: init schema
    CLI->>Repo: create_filing_requests([AAPL])
    Repo->>DB: insert filing_requests(PENDING) and QUEUED step
    CLI->>Pipe: process_filing_request(request)
    Pipe->>Repo: start_processing_request(PROCESSING)
    Pipe->>Repo: find_company_by_ticker(AAPL)
    Repo->>DB: read company_directory
    Pipe->>Repo: record CIK_RESOLVED
    Pipe->>Repo: check successful cached response
    Pipe->>SEC: fetch_latest_10k_metadata(cik)
    SEC->>Limit: acquire()
    SEC->>SEC: GET submissions JSON
    SEC-->>Pipe: FilingMetadata
    Pipe->>Repo: record METADATA_FETCHED
    Pipe->>SEC: download_filing_html(source_url)
    SEC->>Limit: acquire()
    SEC->>SEC: GET archive HTML
    SEC-->>Pipe: HTML string
    Pipe->>PDF: convert_html_to_pdf_atomic(html, target)
    PDF->>FS: write target.pdf.tmp
    PDF->>FS: rename target.pdf.tmp to target.pdf
    Pipe->>Repo: complete_request_success(response)
    Repo->>DB: insert filing_responses(SUCCESS), mark request COMPLETED
    Pipe->>Logs: request_completed JSON line
    Pipe-->>CLI: ProcessResult(SUCCESS, pdf_path)
    CLI-->>User: terminal result table
```

## Bootstrap Sequence

```mermaid
sequenceDiagram
    actor User
    participant CLI as bootstrap command
    participant Repo as Repository
    participant SEC as SEC Client
    participant Limit as Rate Limiter
    participant DB as SQLite

    User->>CLI: uv run k10fetcher bootstrap
    CLI->>DB: init schema
    CLI->>Repo: count company_directory
    alt cache exists and no --force
        CLI-->>User: skip SEC fetch
    else empty cache or --force
        CLI->>SEC: fetch_company_tickers()
        SEC->>Limit: acquire()
        SEC->>SEC: GET company_tickers.json
        SEC-->>CLI: CompanyDirectoryEntry[]
        CLI->>Repo: upsert_company_directory(entries)
        Repo->>DB: upsert by ticker
        CLI-->>User: upsert count
    end
```

## Request Submission Sequence

```mermaid
sequenceDiagram
    participant CLI as fetch command
    participant Repo as Repository
    participant DB as SQLite

    CLI->>Repo: create_filing_requests(raw tickers)
    Repo->>DB: BEGIN
    loop each ticker
        Repo->>DB: insert filing_requests(PENDING)
        Repo->>DB: insert filing_processing_steps(QUEUED)
    end
    Repo->>DB: COMMIT
    Repo-->>CLI: batch_id and FilingRequest[]
```

## Ticker Resolution And Failure Sequence

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant Repo as Repository
    participant DB as SQLite
    participant Log as JSON Log

    Pipe->>Repo: start_processing_request()
    Repo->>DB: set PROCESSING and started_at
    Pipe->>Repo: find_company_by_ticker(ticker)
    alt ticker missing
        Pipe->>Repo: complete_request_failure(INVALID_TICKER)
        Repo->>DB: insert filing_responses(FAILED)
        Repo->>DB: set filing_requests FAILED
        Repo->>DB: insert FAILED processing step
        Pipe->>Log: request_failed
    else ticker found
        Pipe->>Repo: record CIK_RESOLVED
    end
```

## Metadata Fetch Sequence

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant SEC as SEC Client
    participant Limit as Rate Limiter
    participant SECWeb as SEC data.sec.gov
    participant Repo as Repository

    Pipe->>Repo: record METADATA_FETCH_STARTED
    Pipe->>SEC: fetch_latest_10k_metadata(ticker, cik)
    SEC->>Limit: acquire outside DB transaction
    SEC->>SECWeb: GET /submissions/CIK##########.json
    SEC->>SEC: parse recent filings arrays
    alt latest 10-K found
        SEC-->>Pipe: FilingMetadata
        Pipe->>Repo: record METADATA_FETCHED
    else no 10-K
        SEC-->>Pipe: None
        Pipe->>Repo: complete_request_failure(NO_10K_FOUND)
    end
```

## HTML Download And PDF Conversion Sequence

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant SEC as SEC Client
    participant Limit as Rate Limiter
    participant Archive as SEC Archives
    participant PDF as PDF Converter
    participant FS as File System
    participant Repo as Repository

    Pipe->>Repo: record HTML_DOWNLOAD_STARTED
    Pipe->>SEC: download_filing_html(source_url)
    SEC->>Limit: acquire outside DB transaction
    SEC->>Archive: GET filing HTML
    Archive-->>SEC: HTML
    SEC-->>Pipe: HTML
    Pipe->>Repo: record HTML_DOWNLOADED
    Pipe->>Repo: record PDF_CONVERSION_STARTED
    Pipe->>PDF: convert_html_to_pdf_atomic(html, target_pdf)
    PDF->>FS: write target_pdf.tmp
    PDF->>FS: replace target_pdf with target_pdf.tmp
    PDF-->>Pipe: target_pdf
    Pipe->>Repo: record PDF_CONVERTED
```

## Success Persistence Sequence

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant Repo as Repository
    participant DB as SQLite
    participant Log as JSON Log

    Pipe->>Repo: complete_request_success(FilingResponseData)
    Repo->>DB: BEGIN
    Repo->>DB: upsert filing_responses(SUCCESS)
    Repo->>DB: insert OUTPUT_WRITTEN step
    Repo->>DB: insert COMPLETED step
    Repo->>DB: update filing_requests(COMPLETED, completed_at)
    Repo->>DB: COMMIT
    Pipe->>Log: request_completed(duration_ms, pdf_path)
```

## Status Sequence

```mermaid
sequenceDiagram
    actor User
    participant CLI as status command
    participant Repo as Repository
    participant DB as SQLite

    User->>CLI: uv run k10fetcher status --batch-id ...
    CLI->>DB: init schema
    CLI->>Repo: list_recent_statuses(limit, batch_id)
    Repo->>DB: left join filing_requests and filing_responses
    Repo-->>CLI: RecentFilingStatus[]
    CLI-->>User: Rich terminal table
```

## Logging Sequence

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant Logger as structlog
    participant FS as Log File

    Pipe->>Logger: request_processing_started
    Pipe->>Logger: metadata_fetched
    Pipe->>Logger: html_downloaded
    Pipe->>Logger: pdf_converted
    alt success
        Pipe->>Logger: request_completed
    else failure
        Pipe->>Logger: request_failed
    end
    Logger->>FS: append JSON line to .k10fetcher/logs/k10fetcher.log
```

## SQLite Tables

### `company_directory`

Stores SEC ticker-to-CIK mappings. `ticker` is unique; `cik` is not unique because a company can have multiple share classes/tickers.

### `filing_requests`

Stores one row per submitted ticker. Status values: `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`.

### `filing_processing_steps`

Append-only audit trail for each request. Important steps include `QUEUED`, `PROCESSING_STARTED`, `CIK_RESOLVED`, `METADATA_FETCH_STARTED`, `METADATA_FETCHED`, `HTML_DOWNLOAD_STARTED`, `HTML_DOWNLOADED`, `PDF_CONVERSION_STARTED`, `PDF_CONVERTED`, `OUTPUT_WRITTEN`, `COMPLETED`, `FAILED`, and `CACHE_HIT`.

### `filing_responses`

Stores one final row per request, either `SUCCESS` with PDF/file metadata or `FAILED` with an explicit error reason.

## Transaction Model

Database operations are short and explicit:

- Request submission inserts `filing_requests` and `QUEUED` steps in one transaction.
- Processing start updates the request and appends a step in one transaction.
- Each audit step is committed independently.
- Success persistence writes the response, output step, completed step, and completed request state in one transaction.
- Failure persistence writes the failed response, failed request state, and failed step in one transaction.
- SEC HTTP calls and rate-limit waits never run inside SQLite transactions.
- PDF conversion completes before a request is marked `COMPLETED`.

## Security Considerations

| Area | Current control | Notes |
| --- | --- | --- |
| External HTTP | SEC-only endpoints used by code paths | No arbitrary user-provided download URL in CLI. Archive URLs are derived from SEC metadata. |
| Rate limiting | Shared process limiter before SEC requests | Default is 10 requests per second. |
| Local persistence | Project-local `.k10fetcher` directory | Keeps artifacts inspectable during development. Consider permissions if used on shared machines. |
| Logs | JSON-lines operational logs | Logs include tickers, CIKs, SEC accession numbers, paths, and errors. No secrets are expected. |
| User input | Tickers normalized to uppercase | Invalid tickers become explicit failed rows. |
| File writes | PDF output path is derived from normalized metadata | Temp file is removed on conversion failure. |
| Dependencies | `uv` and pinned lock file workflow | Keep dependency updates reviewed because WeasyPrint and HTTP clients are security-relevant. |

## Test Strategy

The automated suite uses mocked SEC responses for deterministic behavior. Key test classes:

- CLI parsing and command behavior.
- SQLite schema creation and idempotence.
- Atomic repository transitions.
- Company directory bootstrap parsing and upsert behavior.
- SEC submissions parsing and archive URL construction.
- Rate limiter behavior without real sleeping.
- HTML download and PDF conversion atomic rename behavior.
- End-to-end fetch success and failure with mocked HTTP.
- JSON-lines logging shape.
- Project-local runtime path defaults.

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
scripts/check.sh
```

## Operational Notes

Recommended first run:

```bash
uv sync
uv run k10fetcher doctor
uv run k10fetcher bootstrap
uv run k10fetcher fetch AAPL META GOOGL
uv run k10fetcher status
```

If tickers fail as `INVALID_TICKER`, check whether `company_directory` is empty and rerun:

```bash
uv run k10fetcher bootstrap --force
```
