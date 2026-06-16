# Parallel Multi-Ticker Fetch

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This plan follows `.agent/PLANS.md`.

## Purpose / Big Picture

The goal is to enhance `k10fetcher fetch` so a command with multiple tickers processes those ticker filing pipelines in parallel. Today the CLI creates one batch and then waits for each ticker to fully finish metadata fetch, HTML download, PDF conversion, and SQLite persistence before starting the next ticker. After this feature, `uv run k10fetcher fetch AAPL META GOOGL` should submit one batch, process independent tickers concurrently, preserve per-request success/failure state in SQLite, honor the shared SEC rate limit, and print a final batch summary table after all workers complete.

The user-visible behavior should stay simple and local: no Redis, Celery, daemon, API server, or external queue. Parallelism is only in-process for one CLI invocation.

## Progress

- [x] (2026-06-15) Drafted the feature plan after inspecting `src/k10fetcher/cli.py`, `src/k10fetcher/pipeline.py`, `src/k10fetcher/repository.py`, `src/k10fetcher/db.py`, `src/k10fetcher/rate_limit.py`, `src/k10fetcher/pdf.py`, and current tests.
- [x] (2026-06-15) Added a thread-safe shared rate limiter.
- [x] (2026-06-15) Added batch-level parallel execution for multiple filing requests.
- [x] (2026-06-15) Made CLI progress output safe and readable when workers run concurrently.
- [x] (2026-06-15) Added focused concurrency tests and reran full validation.
- [x] (2026-06-15) Validated a live multi-ticker sandbox run with batch `87ad3752cac04cc29c832166f2001f62`.

## Surprises & Discoveries

- Observation: `fetch` already accepts and de-duplicates multiple tickers through `_parse_tickers`, but `results = [...]` in `src/k10fetcher/cli.py` processes each request sequentially.
- Observation: repository functions open a fresh SQLite connection per operation, which is compatible with worker threads, but SQLite writes still serialize and should get a busy timeout to reduce transient lock failures.
- Observation: the current `RateLimiter` keeps mutable deque state without a lock; sharing it across worker threads would violate the global SEC rate-limit guarantee.
- Observation: `_progress_step` animates one step at a time and writes carriage-return terminal updates. Concurrent workers would interleave this output unless progress rendering is changed or serialized.
- Observation: `convert_html_to_pdf_atomic` writes to a ticker-specific final path and uses a sibling `.tmp` file. Different tickers should not collide. Duplicate tickers are already de-duplicated before request creation.
- Observation: The repository's `scripts/check.sh` is not executable in this checkout, so validation uses `bash scripts/check.sh`.

## Decision Log

- Decision: Use `concurrent.futures.ThreadPoolExecutor` for the MVP parallel implementation.
  Rationale: The current SEC client, repository, and PDF conversion APIs are synchronous. Threads let the CLI overlap HTTP waits and independent PDF conversions without rewriting the stack to async.
  Date/Author: 2026-06-15 / Codex.

- Decision: Add an explicit CLI worker option while keeping parallel behavior automatic for multiple tickers.
  Rationale: Users get the requested default behavior, and operators can tune local CPU/network pressure. Proposed option: `--workers INTEGER`, defaulting to `min(number_of_requests, 4)`, with validation that workers is at least 1.
  Date/Author: 2026-06-15 / Codex.

- Decision: Keep one shared rate limiter per fetch invocation.
  Rationale: SEC limits should apply to the whole process, not per ticker. The limiter must be made thread-safe with a `threading.Lock` or equivalent.
  Date/Author: 2026-06-15 / Codex.

- Decision: Preserve final result table ordering by submission order, not completion order.
  Rationale: Stable ordering makes CLI output easier to compare with user input while allowing work to complete out of order internally.
  Date/Author: 2026-06-15 / Codex.

## Outcomes & Retrospective

Implementation and live acceptance are complete. The CLI now has a `--workers` option, defaults to parallel workers for multi-ticker batches, preserves final result ordering, and uses a thread-safe shared SEC rate limiter. Full automated validation passed with `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `bash scripts/check.sh`. Live acceptance passed with `uv run k10fetcher fetch --no-cache --workers 3 AAPL META GOOGL`; batch `87ad3752cac04cc29c832166f2001f62` completed with successful PDFs for AAPL, META, and GOOGL.

## Context and Orientation

The repository root is `/Users/manishhmundra/dev/company/local/sec-filing`. The CLI command lives in `src/k10fetcher/cli.py`. The existing `fetch` flow is:

1. Configure logging and initialize SQLite.
2. Normalize ticker arguments.
3. Insert one `filing_requests` row per ticker through `create_filing_requests`.
4. Create one `RateLimiter`.
5. Iterate requests sequentially and call `process_filing_request`.
6. Render a final Rich table.

The per-request pipeline lives in `src/k10fetcher/pipeline.py`. `process_filing_request` is already a useful worker boundary because it owns one filing request and records all state transitions. Repository calls live in `src/k10fetcher/repository.py` and open connections via `src/k10fetcher/db.py`.

## Plan of Work

1. Make shared infrastructure thread-safe.
   - Add a lock to `RateLimiter.acquire` so multiple workers share one global request window correctly.
   - Consider setting a SQLite connection timeout in `connect`, such as `sqlite3.connect(db_path, timeout=30.0)`.
   - Consider enabling `PRAGMA busy_timeout = 30000` and `PRAGMA journal_mode = WAL` during `init_db` so concurrent read/write bursts are less fragile.

2. Introduce a batch execution helper.
   - Add a function such as `process_filing_requests_parallel(...) -> list[ProcessResult]`.
   - Use `ThreadPoolExecutor(max_workers=workers)` and submit one `process_filing_request` call per `FilingRequest`.
   - Capture exceptions from futures and turn them into failed request rows when possible. Unexpected worker crashes should not prevent other tickers from finishing.
   - Return results in the same order as the `requests` input list.

3. Update CLI behavior.
   - Add `--workers` to `fetch`, default `None`.
   - Resolve worker count as `1` for one request, otherwise `min(len(requests), 4)` unless the user provides `--workers`.
   - Print `Workers: N` near the existing rate-limit/no-cache summary.
   - Call the new parallel helper instead of the current list comprehension.

4. Make progress output concurrency-safe.
   - Replace interactive spinner-style `_progress_step` for parallel fetches with simple line-oriented messages protected by a `threading.Lock`, or create a small progress reporter object that serializes `console.print`.
   - Keep non-interactive output deterministic enough for tests: `TICKER | Step started/done/failed`.
   - Avoid multiple worker threads writing carriage-return animation at the same time.

5. Preserve cache and idempotence behavior.
   - Cache hits should still complete quickly per ticker without blocking the batch.
   - Existing PDF paths should remain unchanged.
   - A failure for one ticker should not cancel other tickers.
   - The final table should include every submitted ticker with `SUCCESS` or `FAILED`.

## Concrete Steps

1. Update `src/k10fetcher/rate_limit.py`:
   - Import `threading`.
   - Add `self._lock = threading.Lock()`.
   - Wrap deque cleanup, capacity check, append, and sleep calculation in the lock.
   - Do not sleep while holding the lock; compute the required sleep, release, sleep, and retry.

2. Update `src/k10fetcher/db.py`:
   - Set an explicit SQLite connection timeout.
   - Enable `PRAGMA busy_timeout`.
   - Evaluate `PRAGMA journal_mode = WAL` in `init_db` for the CLI runtime database. If tests using temporary files show platform friction, document the reason and keep the timeout-only approach.

3. Update `src/k10fetcher/pipeline.py`:
   - Add a batch helper around `ThreadPoolExecutor`.
   - Keep `process_filing_request` as the single-request unit.
   - Use a result map keyed by request id or input index so returned results preserve input ordering.

4. Update `src/k10fetcher/cli.py`:
   - Add the `--workers` option.
   - Build a concurrency-safe progress callback.
   - Replace the sequential comprehension with the batch helper.
   - Keep the final Rich table rendering unchanged except for all results now arriving from parallel execution.

5. Update tests:
   - Add a unit test for `RateLimiter` that multiple threads cannot exceed the configured window.
   - Add a CLI or pipeline test that monkeypatches the per-request worker to sleep and asserts multiple requests overlap.
   - Add a test that one worker failure still yields a final result for every ticker.
   - Update output assertions to include `Workers:`.

6. Run validation:
   - `uv run pytest`
   - `uv run ruff check .`
   - `uv run ruff format --check .`
   - `bash scripts/check.sh`
   - `uv run k10fetcher bootstrap`
   - `uv run k10fetcher fetch --no-cache --workers 3 AAPL META GOOGL`
   - `uv run k10fetcher status --batch-id <batch_id>`

## Validation and Acceptance

Automated acceptance:

- Tests pass with the new concurrency coverage.
- Ruff lint and format checks pass.
- The parallel helper returns results in submission order even when workers complete out of order.
- A simulated slow multi-ticker fetch completes materially faster with `--workers 3` than with `--workers 1`.
- A simulated failure in one ticker does not prevent successful completion of other ticker requests.
- The shared rate limiter is demonstrably process-wide across all workers.

Manual/local acceptance:

- `uv run k10fetcher fetch AAPL META GOOGL` prints a single batch id, creates three request rows, reports more than one worker by default, and finishes with a table containing all three tickers.
- The `filing_processing_steps` timestamps for different tickers overlap, showing concurrent processing rather than one full ticker after another.
- PDFs are stored under `.k10fetcher/data/active/{ticker}/10-K/`.
- `uv run k10fetcher status --batch-id <batch_id>` shows all requests completed or failed independently.

## Idempotence and Recovery

If the CLI is interrupted, already-completed ticker requests should remain completed in SQLite and their PDFs should remain in place. Requests still in progress may remain `PROCESSING`; this is pre-existing behavior and can be addressed separately with stale-request recovery if needed. Re-running the same tickers without `--no-cache` should reuse successful cached PDFs. Re-running with `--no-cache` should create a new batch and regenerate PDFs.

SQLite lock contention should be recoverable through connection timeout and short transactions. Worker-level exceptions should be recorded as request failures whenever a request id is available.

## Interfaces and Dependencies

No new external runtime dependencies are required. Use Python standard library concurrency primitives:

- `concurrent.futures.ThreadPoolExecutor`
- `concurrent.futures.as_completed`
- `threading.Lock`

Existing interfaces remain:

- `k10fetcher fetch TICKER [TICKER ...]`
- `--db-path`
- `--data-dir`
- `--rate-limit`
- `--no-cache`
- `--log-path`

New proposed interface:

- `--workers INTEGER`: maximum number of ticker requests to process concurrently. Default is automatic: `1` for one request, otherwise `min(number_of_requests, 4)`.
