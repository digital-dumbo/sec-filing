import time
from pathlib import Path

import respx
from httpx import Response

import k10fetcher.pipeline as pipeline_module
from k10fetcher.db import connect, init_db
from k10fetcher.pipeline import (
    ProcessResult,
    process_filing_request,
    process_filing_requests_parallel,
)
from k10fetcher.rate_limit import RateLimiter
from k10fetcher.repository import (
    CompanyDirectoryEntry,
    FilingResponseData,
    complete_request_success,
    create_filing_requests,
    upsert_company_directory,
)
from k10fetcher.sec_client import submissions_url


def test_process_filing_request_reuses_cache(tmp_path: Path):
    db_path = tmp_path / "k10fetcher.db"
    data_dir = tmp_path / "data"
    cached_pdf = data_dir / "active" / "AAPL" / "10-K" / "cached.pdf"
    cached_pdf.parent.mkdir(parents=True)
    cached_pdf.write_bytes(b"%PDF cached")
    init_db(db_path)
    upsert_company_directory(
        db_path,
        [CompanyDirectoryEntry(cik="0000320193", ticker="AAPL", company_name="Apple Inc.")],
    )
    _, existing_requests = create_filing_requests(db_path, ["AAPL"], batch_id="old")
    complete_request_success(
        db_path,
        existing_requests[0].id,
        FilingResponseData(
            ticker="AAPL",
            cik="0000320193",
            company_name="Apple Inc.",
            form_type="10-K",
            filing_date="2025-10-31",
            accession_number="0000320193-25-000079",
            source_url="https://www.sec.gov/example.htm",
            pdf_path=str(cached_pdf),
        ),
    )
    _, requests = create_filing_requests(db_path, ["AAPL"], batch_id="new")

    result = process_filing_request(
        db_path=db_path,
        data_dir=data_dir,
        request=requests[0],
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=RateLimiter(10),
    )

    assert result.status == "SUCCESS"
    assert result.destination_or_error == str(cached_pdf)


@respx.mock
def test_process_filing_request_records_no_10k_failure(tmp_path: Path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    upsert_company_directory(
        db_path,
        [CompanyDirectoryEntry(cik="0000789019", ticker="MSFT", company_name="Microsoft Corp")],
    )
    _, requests = create_filing_requests(db_path, ["MSFT"], batch_id="batch")
    respx.get(submissions_url("0000789019")).mock(
        return_value=Response(
            200,
            json={"filings": {"recent": {"form": ["10-Q"]}}},
        )
    )

    result = process_filing_request(
        db_path=db_path,
        data_dir=tmp_path / "data",
        request=requests[0],
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=RateLimiter(10),
    )

    assert result.status == "FAILED"
    assert result.destination_or_error.startswith("NO_10K_FOUND")
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT status, error_reason FROM filing_responses WHERE request_id = ?",
            (requests[0].id,),
        ).fetchone()
    assert row["status"] == "FAILED"
    assert row["error_reason"].startswith("NO_10K_FOUND")


def test_process_filing_requests_parallel_overlaps_and_preserves_order(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    _, requests = create_filing_requests(db_path, ["AAPL", "META", "GOOGL"], batch_id="batch")
    starts: dict[str, float] = {}

    def fake_process_filing_request(**kwargs) -> ProcessResult:
        request = kwargs["request"]
        ticker = request.normalized_ticker
        starts[ticker] = time.monotonic()
        time.sleep({"AAPL": 0.08, "META": 0.02, "GOOGL": 0.04}[ticker])
        return ProcessResult(ticker=ticker, status="SUCCESS", destination_or_error=ticker)

    monkeypatch.setattr(
        pipeline_module,
        "process_filing_request",
        fake_process_filing_request,
    )

    started_at = time.monotonic()
    results = process_filing_requests_parallel(
        db_path=db_path,
        data_dir=tmp_path / "data",
        requests=requests,
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=RateLimiter(10),
        workers=3,
    )
    elapsed = time.monotonic() - started_at

    assert [result.ticker for result in results] == ["AAPL", "META", "GOOGL"]
    assert elapsed < 0.13
    assert max(starts.values()) - min(starts.values()) < 0.05


def test_process_filing_requests_parallel_records_isolated_worker_failure(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    _, requests = create_filing_requests(db_path, ["AAPL", "META"], batch_id="batch")

    def fake_process_filing_request(**kwargs) -> ProcessResult:
        request = kwargs["request"]
        ticker = request.normalized_ticker
        if ticker == "META":
            raise RuntimeError("boom")
        return ProcessResult(ticker=ticker, status="SUCCESS", destination_or_error=ticker)

    monkeypatch.setattr(
        pipeline_module,
        "process_filing_request",
        fake_process_filing_request,
    )

    results = process_filing_requests_parallel(
        db_path=db_path,
        data_dir=tmp_path / "data",
        requests=requests,
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=RateLimiter(10),
        workers=2,
    )

    assert [(result.ticker, result.status) for result in results] == [
        ("AAPL", "SUCCESS"),
        ("META", "FAILED"),
    ]
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT requests.status AS request_status,
                   responses.status AS response_status,
                   responses.error_reason
            FROM filing_requests AS requests
            JOIN filing_responses AS responses ON responses.request_id = requests.id
            WHERE requests.normalized_ticker = 'META'
            """
        ).fetchone()

    assert row["request_status"] == "FAILED"
    assert row["response_status"] == "FAILED"
    assert row["error_reason"] == "WORKER_FAILED: boom"
