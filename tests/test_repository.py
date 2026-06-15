import pytest

from k10fetcher.db import connect, init_db, transaction
from k10fetcher.repository import (
    CompanyDirectoryEntry,
    FilingResponseData,
    complete_request_failure,
    complete_request_success,
    count_rows,
    create_filing_requests,
    find_company_by_ticker,
    get_request,
    list_recent_statuses,
    start_processing_request,
    upsert_company_directory,
)


def test_company_directory_upsert_allows_multiple_tickers_for_one_cik(tmp_path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)

    count = upsert_company_directory(
        db_path,
        [
            CompanyDirectoryEntry(cik="0000320193", ticker="AAPL", company_name="Apple Inc."),
            CompanyDirectoryEntry(cik="0000320193", ticker="APPL", company_name="Apple Alias"),
        ],
    )

    assert count == 2
    assert find_company_by_ticker(db_path, "aapl") == CompanyDirectoryEntry(
        cik="0000320193", ticker="AAPL", company_name="Apple Inc."
    )
    assert find_company_by_ticker(db_path, "APPL") == CompanyDirectoryEntry(
        cik="0000320193", ticker="APPL", company_name="Apple Alias"
    )


def test_success_transition_is_atomic(tmp_path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    _, requests = create_filing_requests(db_path, ["aapl"], batch_id="batch-1")
    request = requests[0]

    start_processing_request(db_path, request.id, step="resolve_ticker", message="Resolving AAPL")
    complete_request_success(
        db_path,
        request.id,
        FilingResponseData(
            ticker="AAPL",
            cik="0000320193",
            company_name="Apple Inc.",
            form_type="10-K",
            filing_date="2025-10-31",
            accession_number="0000320193-25-000079",
            source_url="https://www.sec.gov/Archives/example.htm",
            pdf_path="/tmp/aapl.pdf",
        ),
    )

    completed = get_request(db_path, request.id)
    assert completed is not None
    assert completed.status == "COMPLETED"
    assert completed.error_reason is None
    assert count_rows(db_path, "filing_responses") == 1
    assert count_rows(db_path, "filing_processing_steps") == 4


def test_failure_transition_records_response_and_request_error(tmp_path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    _, requests = create_filing_requests(db_path, ["NOPE"], batch_id="batch-1")
    request = requests[0]

    complete_request_failure(
        db_path,
        request.id,
        ticker="NOPE",
        error_reason="Ticker not found in SEC company directory.",
    )

    failed = get_request(db_path, request.id)
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.error_reason == "Ticker not found in SEC company directory."

    with connect(db_path) as connection:
        response = connection.execute(
            "SELECT status, error_reason FROM filing_responses WHERE request_id = ?",
            (request.id,),
        ).fetchone()

    assert response["status"] == "FAILED"
    assert response["error_reason"] == "Ticker not found in SEC company directory."


def test_transaction_rolls_back_partial_writes(tmp_path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)

    with (
        pytest.raises(RuntimeError, match="boom"),
        transaction(db_path) as connection,
    ):
        connection.execute(
            """
            INSERT INTO filing_requests (batch_id, raw_input, normalized_ticker, status)
            VALUES ('batch-rollback', 'MSFT', 'MSFT', 'PENDING')
            """
        )
        raise RuntimeError("boom")

    assert count_rows(db_path, "filing_requests") == 0


def test_list_recent_statuses_returns_request_response_view(tmp_path):
    db_path = tmp_path / "k10fetcher.db"
    init_db(db_path)
    _, requests = create_filing_requests(db_path, ["AAPL", "MSFT"], batch_id="batch-1")
    complete_request_failure(
        db_path,
        requests[0].id,
        ticker="AAPL",
        error_reason="Example failure",
    )

    rows = list_recent_statuses(db_path, limit=2)

    assert [row.ticker for row in rows] == ["MSFT", "AAPL"]
    assert rows[0].request_status == "PENDING"
    assert rows[0].response_status is None
    assert rows[1].request_status == "FAILED"
    assert rows[1].response_status == "FAILED"
    assert rows[1].error_reason == "Example failure"
