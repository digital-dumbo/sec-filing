import json
import sqlite3
from pathlib import Path

import respx
from httpx import Response
from typer.testing import CliRunner

from k10fetcher.cli import _parse_tickers, app
from k10fetcher.db import init_db
from k10fetcher.repository import CompanyDirectoryEntry, upsert_company_directory
from k10fetcher.sec_client import COMPANY_TICKERS_URL, filing_source_url, submissions_url

runner = CliRunner()


def test_parse_tickers_accepts_space_and_comma_separated_values() -> None:
    assert _parse_tickers((" aapl,META", "googl", "AAPL")) == ["AAPL", "META", "GOOGL"]


@respx.mock
def test_bootstrap_initializes_sqlite_schema_and_company_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"
    respx.get(COMPANY_TICKERS_URL).mock(
        return_value=Response(
            200,
            json={
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
            },
        )
    )

    result = runner.invoke(app, ["bootstrap", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert db_path.exists()
    assert "SQLite runtime" in result.output
    assert "Database ready" in result.output
    assert "Company directory fetch" in result.output
    assert "Upserted 2 ticker mappings" in result.output

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT cik, ticker, company_name FROM company_directory ORDER BY ticker"
        ).fetchall()

    assert rows == [
        ("0000320193", "AAPL", "Apple Inc."),
        ("0000789019", "MSFT", "Microsoft Corp"),
    ]


def test_doctor_reports_runtime_steps(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"

    result = runner.invoke(app, ["doctor", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Python SQLite driver" in result.output
    assert "Database path" in result.output
    assert "Runtime checks completed" in result.output


def test_fetch_validates_empty_ticker_input() -> None:
    result = runner.invoke(app, ["fetch", ""])

    assert result.exit_code != 0


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"

    init_db(db_path)
    init_db(db_path)

    assert db_path.exists()


def test_init_db_creates_request_step_response_tables(tmp_path):
    db_path = tmp_path / "filings.db"

    init_db(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "filing_requests",
        "filing_processing_steps",
        "filing_responses",
    }.issubset(tables)
    assert not {
        "filing_inputs",
        "filing_processing_events",
        "filing_outputs",
    }.intersection(tables)


def test_fetch_records_invalid_ticker_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"

    result = runner.invoke(app, ["fetch", "NOPE", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Batch submitted:" in result.output
    assert "NOPE | Resolve ticker" in result.output
    assert "INVALID_TICKER" in result.output

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT requests.normalized_ticker, requests.status,
                   responses.status, responses.error_reason
            FROM filing_requests AS requests
            JOIN filing_responses AS responses ON responses.request_id = requests.id
            """
        ).fetchall()

    assert rows == [
        ("NOPE", "FAILED", "FAILED", "INVALID_TICKER: not found in SEC company directory")
    ]


def test_status_renders_recent_requests(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"
    runner.invoke(app, ["fetch", "AAPL", "--db-path", str(db_path)])

    result = runner.invoke(app, ["status", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Recent filing requests" in result.output
    assert "AAPL" in result.output
    assert "FAILED" in result.output


@respx.mock
def test_bootstrap_skips_company_fetch_when_cache_exists(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"
    respx.get(COMPANY_TICKERS_URL).mock(
        return_value=Response(
            200,
            json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
        )
    )
    runner.invoke(app, ["bootstrap", "--db-path", str(db_path)])

    result = runner.invoke(app, ["bootstrap", "--db-path", str(db_path)])

    assert result.exit_code == 0
    assert "Using existing cache with 1 rows" in result.output
    assert "No SEC request was made" in result.output


@respx.mock
def test_fetch_end_to_end_success(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "k10fetcher.db"
    data_dir = tmp_path / "data"
    init_db(db_path)
    upsert_company_directory(
        db_path,
        [CompanyDirectoryEntry(cik="0000320193", ticker="AAPL", company_name="Apple Inc.")],
    )
    source_url = filing_source_url("0000320193", "0000320193-25-000079", "aapl.htm")
    respx.get(submissions_url("0000320193")).mock(
        return_value=Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": ["2025-10-31"],
                        "accessionNumber": ["0000320193-25-000079"],
                        "primaryDocument": ["aapl.htm"],
                    }
                }
            },
        )
    )
    respx.get(source_url).mock(return_value=Response(200, text="<html>AAPL filing</html>"))

    def fake_convert(html: str, target_pdf: Path, *, base_url: str | None = None) -> Path:
        assert html == "<html>AAPL filing</html>"
        assert base_url == source_url
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        target_pdf.write_bytes(b"%PDF fake")
        return target_pdf

    monkeypatch.setattr("k10fetcher.pipeline.convert_html_to_pdf_atomic", fake_convert)

    result = runner.invoke(
        app,
        [
            "fetch",
            "AAPL",
            "--db-path",
            str(db_path),
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0
    assert "AAPL | Fetch metadata" in result.output
    assert "AAPL | Convert PDF" in result.output
    assert "SUCCESS" in result.output

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT requests.status, responses.status, responses.pdf_path, responses.accession_number
            FROM filing_requests AS requests
            JOIN filing_responses AS responses ON responses.request_id = requests.id
            """
        ).fetchone()

    assert row[0] == "COMPLETED"
    assert row[1] == "SUCCESS"
    assert row[2].endswith("AAPL_10-K_2025-10-31.pdf")
    assert row[3] == "0000320193-25-000079"


def test_fetch_writes_json_log_lines(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"
    log_path = tmp_path / "logs" / "k10fetcher.log"

    result = runner.invoke(
        app,
        [
            "fetch",
            "NOPE",
            "--db-path",
            str(db_path),
            "--log-path",
            str(log_path),
        ],
    )

    assert result.exit_code == 0
    lines = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert any(line["event"] == "request_processing_started" for line in lines)
    failed = next(line for line in lines if line["event"] == "request_failed")
    assert failed["ticker"] == "NOPE"
    assert failed["step"] == "FAILED"
    assert failed["error"].startswith("INVALID_TICKER")


def test_status_filters_by_batch_id(tmp_path: Path) -> None:
    db_path = tmp_path / "k10fetcher.db"
    runner.invoke(app, ["fetch", "AAPL", "--db-path", str(db_path)])
    runner.invoke(app, ["fetch", "MSFT", "--db-path", str(db_path)])

    with sqlite3.connect(db_path) as connection:
        batch_id = connection.execute(
            "SELECT batch_id FROM filing_requests WHERE normalized_ticker = 'AAPL'"
        ).fetchone()[0]

    result = runner.invoke(
        app,
        ["status", "--db-path", str(db_path), "--batch-id", batch_id],
    )

    assert result.exit_code == 0
    assert "AAPL" in result.output
    assert "MSFT" not in result.output
