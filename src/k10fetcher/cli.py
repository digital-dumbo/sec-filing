import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from k10fetcher.config import settings
from k10fetcher.db import init_db
from k10fetcher.logging import configure_logging
from k10fetcher.pipeline import process_filing_request
from k10fetcher.rate_limit import RateLimiter
from k10fetcher.repository import (
    count_rows,
    create_filing_requests,
    list_recent_statuses,
    upsert_company_directory,
)
from k10fetcher.sec_client import fetch_company_tickers

app = typer.Typer(help="Fetch latest SEC 10-K filings into local PDFs.")
console = Console()


def _parse_tickers(values: tuple[str, ...]) -> list[str]:
    tickers: list[str] = []
    for value in values:
        for part in value.replace("\n", ",").split(","):
            ticker = part.strip().upper()
            if ticker and ticker not in tickers:
                tickers.append(ticker)
    return tickers


def _print_step(number: int, total: int, title: str, detail: str, *, status: str = "OK") -> None:
    console.print(f"[{number}/{total}] {title}: {status}")
    console.print(f"    {detail}")


def _ensure_runtime_paths(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _progress_step(ticker: str, step: str) -> Iterator[None]:
    label = f"{ticker} | {step}"
    stream = console.file
    is_interactive = bool(getattr(stream, "isatty", lambda: False)())
    if not is_interactive:
        console.print(f"{label} started")
        try:
            yield
        except Exception:
            console.print(f"{label} failed")
            raise
        console.print(f"{label} done")
        return

    stop_event = threading.Event()

    def write_progress(text: str) -> None:
        stream.write(f"\r\033[2K{text}")
        stream.flush()

    def animate() -> None:
        dot_states = (".", "..", "...")
        index = 0
        while not stop_event.is_set():
            write_progress(f"{label}{dot_states[index % len(dot_states)]}")
            index += 1
            time.sleep(0.35)

    thread = threading.Thread(target=animate, daemon=True)
    thread.start()
    try:
        yield
    except Exception:
        stop_event.set()
        thread.join()
        write_progress(f"{label} failed")
        stream.write("\n")
        stream.flush()
        raise
    stop_event.set()
    thread.join()
    write_progress(f"{label} done")
    stream.write("\n")
    stream.flush()


@app.callback()
def main() -> None:
    """Local SQLite-only CLI. No Redis, Celery, broker, or web server."""


@app.command()
def doctor(
    db_path: Annotated[Path, typer.Option(help="SQLite database path.")] = settings.db_path,
    log_path: Annotated[Path, typer.Option(help="JSON-lines log path.")] = settings.log_path,
) -> None:
    """Check local runtime prerequisites and paths."""
    configure_logging(log_path)
    _ensure_runtime_paths(db_path)
    _print_step(
        1,
        4,
        "Python SQLite driver",
        f"sqlite3 module available; SQLite library version {sqlite3.sqlite_version}.",
    )
    _print_step(2, 4, "Application directory", str(settings.app_dir))
    _print_step(3, 4, "Database path", str(db_path))
    _print_step(
        4,
        4,
        "SEC rate limit",
        f"Configured for {settings.sec_rate_limit_per_second} requests/second.",
    )
    console.print("Runtime checks completed.")


@app.command()
def bootstrap(
    db_path: Annotated[Path, typer.Option(help="SQLite database path.")] = settings.db_path,
    log_path: Annotated[Path, typer.Option(help="JSON-lines log path.")] = settings.log_path,
    force: Annotated[bool, typer.Option(help="Refresh existing directory rows.")] = False,
) -> None:
    """Initialize the database and prepare for SEC company directory bootstrap."""
    configure_logging(log_path)
    mode = "refresh" if force else "initialize"
    _print_step(1, 5, "Runtime paths", f"Preparing local app directories under {settings.app_dir}.")
    _ensure_runtime_paths(db_path)
    _print_step(
        2,
        5,
        "SQLite runtime",
        f"Using Python sqlite3 with SQLite library version {sqlite3.sqlite_version}.",
    )
    init_db(db_path)
    _print_step(3, 5, "SQLite schema", f"Database ready at {db_path} ({mode} mode).")

    existing_count = count_rows(db_path, "company_directory")
    if existing_count and not force:
        _print_step(
            4,
            5,
            "Company directory cache",
            f"Using existing cache with {existing_count} rows. Pass --force to refresh.",
            status="SKIPPED",
        )
        _print_step(5, 5, "Bootstrap summary", "No SEC request was made.")
        return

    _print_step(
        4,
        5,
        "Company directory fetch",
        "Fetching SEC company_tickers.json with configured User-Agent.",
    )
    limiter = RateLimiter(settings.sec_rate_limit_per_second)
    entries = fetch_company_tickers(
        user_agent=settings.sec_user_agent,
        rate_limiter=limiter,
    )
    upserted_count = upsert_company_directory(db_path, entries)
    _print_step(
        5,
        5,
        "Company directory cache",
        f"Upserted {upserted_count} ticker mappings into SQLite.",
    )
    console.print("Bootstrap completed.")


@app.command()
def fetch(
    tickers: Annotated[
        list[str],
        typer.Argument(help="Tickers, space or comma separated."),
    ],
    db_path: Annotated[Path, typer.Option(help="SQLite database path.")] = settings.db_path,
    data_dir: Annotated[Path, typer.Option(help="PDF output data directory.")] = settings.data_dir,
    rate_limit: Annotated[int, typer.Option(help="Max SEC requests per second.")] = 10,
    no_cache: Annotated[
        bool,
        typer.Option(help="Ignore existing successful outputs."),
    ] = False,
    log_path: Annotated[Path, typer.Option(help="JSON-lines log path.")] = settings.log_path,
) -> None:
    """Submit and process one local filing fetch batch."""
    configure_logging(log_path)
    init_db(db_path)
    normalized = _parse_tickers(tuple(tickers))
    if not normalized:
        raise typer.BadParameter("Enter at least one ticker.")

    data_dir.mkdir(parents=True, exist_ok=True)
    batch_id, requests = create_filing_requests(db_path, normalized)
    console.print(f"Batch submitted: {batch_id}")
    console.print(f"Requests created: {len(requests)}")
    submitted_tickers = ", ".join(
        request.normalized_ticker or request.raw_input for request in requests
    )
    console.print(f"Tickers: {submitted_tickers}")
    console.print(f"Rate limit: {rate_limit} SEC requests/second; no_cache={no_cache}")

    limiter = RateLimiter(rate_limit)
    results = [
        process_filing_request(
            db_path=db_path,
            data_dir=data_dir,
            request=request,
            user_agent=settings.sec_user_agent,
            rate_limiter=limiter,
            no_cache=no_cache,
            progress=_progress_step,
        )
        for request in requests
    ]

    table = Table(title=f"Batch: {batch_id}")
    table.add_column("Input")
    table.add_column("Status")
    table.add_column("Destination / Error")
    for result in results:
        table.add_row(result.ticker, result.status, result.destination_or_error)
    console.print(table)


@app.command()
def status(
    db_path: Annotated[Path, typer.Option(help="SQLite database path.")] = settings.db_path,
    limit: Annotated[int, typer.Option(help="Number of recent rows to show.")] = 20,
    batch_id: Annotated[str | None, typer.Option(help="Filter by batch id.")] = None,
    log_path: Annotated[Path, typer.Option(help="JSON-lines log path.")] = settings.log_path,
) -> None:
    """Show recent local request status."""
    configure_logging(log_path)
    init_db(db_path)
    rows = list_recent_statuses(db_path, limit=limit, batch_id=batch_id)
    if not rows:
        console.print(f"No filing requests found in {db_path}.")
        return

    table = Table(title=f"Recent filing requests ({db_path})")
    table.add_column("ID", justify="right")
    table.add_column("Batch")
    table.add_column("Ticker")
    table.add_column("Request")
    table.add_column("Response")
    table.add_column("Filing Date")
    table.add_column("PDF")
    table.add_column("Error")

    for row in rows:
        table.add_row(
            str(row.request_id),
            row.batch_id[:12],
            row.ticker or "-",
            row.request_status,
            row.response_status or "-",
            row.filing_date or "-",
            row.pdf_path or "-",
            row.error_reason or "-",
        )

    console.print(table)