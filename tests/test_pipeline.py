from pathlib import Path

import respx
from httpx import Response

from k10fetcher.db import connect, init_db
from k10fetcher.pipeline import process_filing_request
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