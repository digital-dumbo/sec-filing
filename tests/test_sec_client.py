import pytest
import respx
from httpx import Response

from k10fetcher.repository import CompanyDirectoryEntry
from k10fetcher.sec_client import (
    COMPANY_TICKERS_URL,
    FilingMetadata,
    download_filing_html,
    fetch_company_tickers,
    fetch_latest_10k_metadata,
    filing_source_url,
    parse_company_tickers,
    parse_latest_10k_metadata,
    submissions_url,
)


class FakeRateLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self) -> None:
        self.calls += 1


def test_parse_company_tickers_normalizes_cik_and_ticker():
    entries = parse_company_tickers(
        {
            "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
            "1": {"cik_str": "789019", "ticker": "MSFT", "title": "Microsoft Corp"},
            "2": {"cik_str": 1, "ticker": "", "title": "Missing ticker"},
        }
    )

    assert entries == [
        CompanyDirectoryEntry(cik="0000320193", ticker="AAPL", company_name="Apple Inc."),
        CompanyDirectoryEntry(cik="0000789019", ticker="MSFT", company_name="Microsoft Corp"),
    ]


@respx.mock
def test_fetch_company_tickers_sends_user_agent_and_uses_rate_limiter():
    limiter = FakeRateLimiter()
    route = respx.get(COMPANY_TICKERS_URL).mock(
        return_value=Response(
            200,
            json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
        )
    )

    entries = fetch_company_tickers(
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=limiter,
    )

    assert entries == [
        CompanyDirectoryEntry(cik="0000320193", ticker="AAPL", company_name="Apple Inc.")
    ]
    assert limiter.calls == 1
    assert route.calls.last.request.headers["user-agent"] == "ExampleOrg ops@example.com"


@respx.mock
def test_fetch_company_tickers_rejects_non_object_payload():
    respx.get(COMPANY_TICKERS_URL).mock(return_value=Response(200, json=[]))

    with pytest.raises(ValueError, match="JSON object"):
        fetch_company_tickers(user_agent="ExampleOrg ops@example.com")


def test_parse_latest_10k_metadata_returns_first_recent_10k():
    metadata = parse_latest_10k_metadata(
        {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-K", "10-Q"],
                    "filingDate": ["2026-01-01", "2025-10-31", "2025-07-30"],
                    "accessionNumber": [
                        "0000320193-26-000001",
                        "0000320193-25-000079",
                        "0000320193-25-000055",
                    ],
                    "primaryDocument": ["a8-k.htm", "aapl-20250927.htm", "a10-q.htm"],
                }
            }
        },
        ticker="aapl",
        cik="320193",
        company_name="Apple Inc.",
    )

    assert metadata == FilingMetadata(
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        form_type="10-K",
        filing_date="2025-10-31",
        accession_number="0000320193-25-000079",
        primary_document="aapl-20250927.htm",
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
        ),
    )


def test_parse_latest_10k_metadata_returns_none_when_missing():
    metadata = parse_latest_10k_metadata(
        {"filings": {"recent": {"form": ["10-Q"]}}},
        ticker="MSFT",
        cik="789019",
        company_name="Microsoft Corp",
    )

    assert metadata is None


def test_filing_source_url_normalizes_archive_path():
    assert filing_source_url("0000320193", "0000320193-25-000079", "aapl-20250927.htm") == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    )


@respx.mock
def test_fetch_latest_10k_metadata_uses_rate_limiter_and_user_agent():
    limiter = FakeRateLimiter()
    route = respx.get(submissions_url("0000320193")).mock(
        return_value=Response(
            200,
            json={
                "filings": {
                    "recent": {
                        "form": ["10-K"],
                        "filingDate": ["2025-10-31"],
                        "accessionNumber": ["0000320193-25-000079"],
                        "primaryDocument": ["aapl-20250927.htm"],
                    }
                }
            },
        )
    )

    metadata = fetch_latest_10k_metadata(
        user_agent="ExampleOrg ops@example.com",
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        rate_limiter=limiter,
    )

    assert metadata is not None
    assert metadata.accession_number == "0000320193-25-000079"
    assert limiter.calls == 1
    assert route.calls.last.request.headers["user-agent"] == "ExampleOrg ops@example.com"


@respx.mock
def test_fetch_latest_10k_metadata_rejects_non_object_payload():
    respx.get(submissions_url("0000320193")).mock(return_value=Response(200, json=[]))

    with pytest.raises(ValueError, match="JSON object"):
        fetch_latest_10k_metadata(
            user_agent="ExampleOrg ops@example.com",
            ticker="AAPL",
            cik="0000320193",
            company_name="Apple Inc.",
        )


@respx.mock
def test_download_filing_html_uses_rate_limiter_and_user_agent():
    limiter = FakeRateLimiter()
    url = "https://www.sec.gov/Archives/edgar/data/320193/example.htm"
    route = respx.get(url).mock(return_value=Response(200, text="<html>filing</html>"))

    html = download_filing_html(
        url=url,
        user_agent="ExampleOrg ops@example.com",
        rate_limiter=limiter,
    )

    assert html == "<html>filing</html>"
    assert limiter.calls == 1
    assert route.calls.last.request.headers["user-agent"] == "ExampleOrg ops@example.com"
