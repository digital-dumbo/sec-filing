from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from k10fetcher.repository import CompanyDirectoryEntry

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"


class RateLimiterProtocol(Protocol):
    def acquire(self) -> None: ...


@dataclass(frozen=True)
class FilingMetadata:
    ticker: str
    cik: str
    company_name: str
    form_type: str
    filing_date: str
    accession_number: str
    primary_document: str
    source_url: str


def _normalize_cik(value: Any) -> str:
    return str(value).strip().zfill(10)


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": user_agent,
    }


def _acquire(rate_limiter: RateLimiterProtocol | None) -> None:
    if rate_limiter is not None:
        rate_limiter.acquire()


def parse_company_tickers(payload: dict[str, Any]) -> list[CompanyDirectoryEntry]:
    entries: list[CompanyDirectoryEntry] = []
    for row in payload.values():
        if not isinstance(row, dict):
            continue

        cik = row.get("cik_str")
        ticker = str(row.get("ticker", "")).strip().upper()
        company_name = str(row.get("title", "")).strip()
        if cik is None or not ticker or not company_name:
            continue

        entries.append(
            CompanyDirectoryEntry(
                cik=_normalize_cik(cik),
                ticker=ticker,
                company_name=company_name,
            )
        )

    return entries


def submissions_url(cik: str) -> str:
    return f"{SUBMISSIONS_BASE_URL}/CIK{_normalize_cik(cik)}.json"


def filing_source_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_path = str(int(_normalize_cik(cik)))
    accession_path = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{accession_path}/{primary_document}"


def parse_latest_10k_metadata(
    payload: dict[str, Any],
    *,
    ticker: str,
    cik: str,
    company_name: str,
) -> FilingMetadata | None:
    recent = payload.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        return None

    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    for index, form_type in enumerate(forms):
        if form_type != "10-K":
            continue

        try:
            filing_date = filing_dates[index]
            accession_number = accession_numbers[index]
        except IndexError:
            return None

        primary_document = ""
        if index < len(primary_documents):
            primary_document = str(primary_documents[index]).strip()
        if not primary_document:
            primary_document = f"{accession_number}-index.html"

        return FilingMetadata(
            ticker=ticker.upper(),
            cik=_normalize_cik(cik),
            company_name=company_name,
            form_type="10-K",
            filing_date=str(filing_date),
            accession_number=str(accession_number),
            primary_document=primary_document,
            source_url=filing_source_url(cik, str(accession_number), primary_document),
        )

    return None


def fetch_company_tickers(
    *,
    user_agent: str,
    timeout_seconds: float = 30.0,
    rate_limiter: RateLimiterProtocol | None = None,
) -> list[CompanyDirectoryEntry]:
    _acquire(rate_limiter)
    response = httpx.get(COMPANY_TICKERS_URL, headers=_headers(user_agent), timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("SEC company tickers response must be a JSON object")
    return parse_company_tickers(payload)


def fetch_latest_10k_metadata(
    *,
    user_agent: str,
    ticker: str,
    cik: str,
    company_name: str,
    timeout_seconds: float = 30.0,
    rate_limiter: RateLimiterProtocol | None = None,
) -> FilingMetadata | None:
    _acquire(rate_limiter)
    response = httpx.get(
        submissions_url(cik), headers=_headers(user_agent), timeout=timeout_seconds
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("SEC submissions response must be a JSON object")
    return parse_latest_10k_metadata(
        payload,
        ticker=ticker,
        cik=cik,
        company_name=company_name,
    )


def download_filing_html(
    *,
    url: str,
    user_agent: str,
    timeout_seconds: float = 30.0,
    rate_limiter: RateLimiterProtocol | None = None,
) -> str:
    _acquire(rate_limiter)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": user_agent,
    }
    response = httpx.get(url, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()
    return response.text