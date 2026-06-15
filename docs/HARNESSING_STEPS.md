# Harnessing Steps for `sec-filing`

This guide lets the user steer the CLI MVP one milestone at a time. Each milestone should end with runnable checks and an observable behavior.

## Milestone 0: Project Sanity

Goal: verify the scaffold is usable.

    cd /Users/manishhmundra/dev/company/sec-filing
    uv sync
    uv run k10fetcher --help
    uv run pytest
    uv run ruff check .

Expected result: the CLI help renders, tests pass, and Ruff reports no issues.

## Milestone 1: SQLite Repository Layer

Implement repository functions for `company_directory`, `filing_requests`, `filing_processing_steps`, and `filing_responses`. Add tests proving atomic success and failure transitions.

## Milestone 2: CLI Submission and Status

Implement `fetch` so it inserts input rows and prints a batch id. Implement `status` so it renders recent input/output rows in a terminal table.

## Milestone 3: SEC Company Directory Bootstrap

Implement `bootstrap` to fetch `https://www.sec.gov/files/company_tickers.json`, normalize CIKs and tickers, and upsert all mappings.

## Milestone 4: SEC Metadata Fetch and Rate Limiter

Implement the 10 requests/second process-wide limiter and latest 10-K metadata discovery.

## Milestone 5: HTML Download and PDF Conversion

Implement archive HTML download and PDF conversion to a temporary file followed by atomic rename.

## Milestone 6: End-to-End Fetch

Wire the full fetch flow: resolve input, cache check, metadata fetch, HTML download, PDF conversion, output insert, and input completion.

## Milestone 7: Logging and Operator Polish

Ensure JSON-lines logs include batch id, input id, ticker, step, duration, and error.

## Milestone 8: Final MVP Acceptance

    uv run pytest
    uv run ruff check .
    uv run ruff format --check .
    scripts/check.sh
    uv run k10fetcher bootstrap
    uv run k10fetcher fetch AAPL META GOOGL
    uv run k10fetcher status

Acceptance: the CLI completes without Redis, Celery, a web server, or a daemon.