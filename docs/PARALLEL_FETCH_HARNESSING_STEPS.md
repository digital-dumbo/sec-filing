# Harnessing Steps for Parallel Multi-Ticker Fetch

This guide lets the user steer the parallel fetch enhancement one milestone at a time. It is based on `.agent/execplans/parallel_fetch_execplan.md`. Each milestone should end with runnable checks and an observable behavior.

## Milestone 0: Baseline Sanity

Goal: verify the current CLI and test suite before changing behavior.

    cd /Users/manishhmundra/dev/company/local/sec-filing
    uv sync
    bash scripts/check.sh

Expected result: Ruff lint passes, Ruff format passes, tests pass, and `k10fetcher doctor` reports runtime OK.

## Milestone 1: Thread-Safe Shared Runtime

Goal: make shared runtime pieces safe for in-process worker threads.

- Make `RateLimiter` safe to share across worker threads.
- Add SQLite busy timeout handling so short concurrent writes wait instead of failing immediately.
- Keep existing public interfaces unchanged.

Checks:

    uv run pytest tests/test_rate_limit.py tests/test_repository.py
    uv run ruff check src/k10fetcher/rate_limit.py src/k10fetcher/db.py

Expected result: existing rate-limit and repository behavior still passes, with new coverage proving shared rate limiting is process-wide.

## Milestone 2: Parallel Batch Executor

Goal: add an in-process batch worker that runs one filing request per worker while preserving batch semantics.

- Keep `process_filing_request` as the single-request unit.
- Add a batch helper that uses `ThreadPoolExecutor`.
- Return final `ProcessResult` rows in original submission order.
- Ensure a single request failure does not stop other requests in the same batch.

Checks:

    uv run pytest tests/test_pipeline.py
    uv run ruff check src/k10fetcher/pipeline.py

Expected result: pipeline tests prove concurrent requests overlap, ordered results are preserved, and isolated failures are recorded.

## Milestone 3: CLI Integration

Goal: make `k10fetcher fetch AAPL META GOOGL` process tickers concurrently by default.

- Add `--workers INTEGER`.
- Default to `1` worker for one request and `min(request_count, 4)` for multiple requests.
- Keep one shared SEC rate limiter for the whole batch.
- Serialize progress output so concurrent workers do not interleave terminal writes.
- Print the worker count in the fetch summary.

Checks:

    uv run pytest tests/test_scaffold.py
    uv run k10fetcher fetch NOPE AAPL --workers 2

Expected result: the CLI accepts the new option, displays `Workers: 2`, and reports a final table for every submitted ticker.

## Milestone 4: Full Automated Validation

Goal: verify the repository is still clean and formatted.

    uv run pytest
    uv run ruff check .
    uv run ruff format --check .
    bash scripts/check.sh

Expected result: all automated checks pass.

## Milestone 5: Live Sandbox Acceptance

Goal: validate real SEC metadata fetch, HTML download, PDF conversion, persistence, and status with parallel workers.

    uv run k10fetcher bootstrap
    uv run k10fetcher fetch --no-cache --workers 3 AAPL META GOOGL
    uv run k10fetcher status --batch-id <batch_id>

Expected result: one batch contains three ticker requests, progress for different tickers overlaps, all finished requests are represented in the final table, generated PDFs are stored under `.k10fetcher/data/active/{ticker}/10-K/`, and status shows each ticker completed or failed independently.
