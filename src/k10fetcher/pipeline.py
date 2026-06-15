from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import httpx

from k10fetcher.logging import get_logger
from k10fetcher.pdf import build_pdf_path, convert_html_to_pdf_atomic
from k10fetcher.rate_limit import RateLimiter
from k10fetcher.repository import (
    FilingRequest,
    FilingResponseData,
    complete_request_failure,
    complete_request_success,
    find_company_by_ticker,
    find_latest_successful_response,
    record_processing_step,
    start_processing_request,
)
from k10fetcher.sec_client import (
    download_filing_html,
    fetch_latest_10k_metadata,
)


@dataclass(frozen=True)
class ProcessResult:
    ticker: str
    status: str
    destination_or_error: str


StepProgress = Callable[[str, str], AbstractContextManager[None]]


def _no_progress(_ticker: str, _step: str) -> AbstractContextManager[None]:
    return nullcontext()


def _failure_reason(code: str, message: str) -> str:
    return f"{code}: {message}"


def _http_error_reason(exc: httpx.HTTPError) -> str:
    return _failure_reason("SEC_HTTP_ERROR", str(exc))


def process_filing_request(
    *,
    db_path: Path,
    data_dir: Path,
    request: FilingRequest,
    user_agent: str,
    rate_limiter: RateLimiter,
    no_cache: bool = False,
    progress: StepProgress = _no_progress,
) -> ProcessResult:
    started_at = time.monotonic()
    ticker = request.normalized_ticker or request.raw_input.strip().upper()
    logger = get_logger(
        batch_id=request.batch_id,
        request_id=request.id,
        ticker=ticker,
    )
    logger.info("request_processing_started", step="PROCESSING_STARTED")
    with progress(ticker, "Start processing"):
        start_processing_request(
            db_path,
            request.id,
            step="PROCESSING_STARTED",
            message=f"Started processing {ticker}.",
        )

    with progress(ticker, "Resolve ticker"):
        company = find_company_by_ticker(db_path, ticker)
    if company is None:
        reason = _failure_reason("INVALID_TICKER", "not found in SEC company directory")
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)

    record_processing_step(
        db_path,
        request.id,
        step="CIK_RESOLVED",
        message=f"Resolved {ticker} to CIK {company.cik}.",
    )

    cached = None
    if not no_cache:
        with progress(ticker, "Check cache"):
            cached = find_latest_successful_response(db_path, ticker=ticker)
        if cached is not None and Path(cached.pdf_path).exists():
            complete_request_success(
                db_path,
                request.id,
                FilingResponseData(
                    ticker=cached.ticker,
                    cik=cached.cik,
                    company_name=cached.company_name,
                    form_type=cached.form_type,
                    filing_date=cached.filing_date,
                    accession_number=cached.accession_number,
                    source_url=cached.source_url,
                    pdf_path=cached.pdf_path,
                ),
                step="CACHE_HIT",
                message=f"Reused existing PDF at {cached.pdf_path}.",
            )
            duration_ms = int((time.monotonic() - started_at) * 1000)
            logger.info("cache_hit", step="CACHE_HIT", duration_ms=duration_ms)
            return ProcessResult(
                ticker=ticker,
                status="SUCCESS",
                destination_or_error=cached.pdf_path,
            )

    try:
        record_processing_step(
            db_path,
            request.id,
            step="METADATA_FETCH_STARTED",
            message="Fetching SEC submissions metadata.",
        )
        with progress(ticker, "Fetch metadata"):
            metadata = fetch_latest_10k_metadata(
                user_agent=user_agent,
                ticker=ticker,
                cik=company.cik,
                company_name=company.company_name,
                rate_limiter=rate_limiter,
            )
    except httpx.HTTPError as exc:
        reason = _http_error_reason(exc)
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)

    if metadata is None:
        reason = _failure_reason("NO_10K_FOUND", "no recent 10-K found in SEC submissions")
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)

    record_processing_step(
        db_path,
        request.id,
        step="METADATA_FETCHED",
        message=f"Found {metadata.accession_number} filed {metadata.filing_date}.",
    )
    logger.info(
        "metadata_fetched",
        step="METADATA_FETCHED",
        accession_number=metadata.accession_number,
        filing_date=metadata.filing_date,
    )

    try:
        record_processing_step(
            db_path,
            request.id,
            step="HTML_DOWNLOAD_STARTED",
            message=f"Downloading {metadata.source_url}.",
        )
        with progress(ticker, "Download filing HTML"):
            html = download_filing_html(
                url=metadata.source_url,
                user_agent=user_agent,
                rate_limiter=rate_limiter,
            )
        record_processing_step(
            db_path,
            request.id,
            step="HTML_DOWNLOADED",
            message="Downloaded filing HTML.",
        )
        logger.info("html_downloaded", step="HTML_DOWNLOADED")

        target_pdf = build_pdf_path(data_dir, metadata)
        record_processing_step(
            db_path,
            request.id,
            step="PDF_CONVERSION_STARTED",
            message=f"Converting filing HTML to {target_pdf}.",
        )
        with progress(ticker, "Convert PDF"):
            pdf_path = convert_html_to_pdf_atomic(html, target_pdf, base_url=metadata.source_url)
        record_processing_step(
            db_path,
            request.id,
            step="PDF_CONVERTED",
            message=f"PDF written to {pdf_path}.",
        )
        logger.info("pdf_converted", step="PDF_CONVERTED", pdf_path=str(pdf_path))
    except httpx.HTTPError as exc:
        reason = _http_error_reason(exc)
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)
    except OSError as exc:
        reason = _failure_reason("FILESYSTEM_ERROR", str(exc))
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)
    except Exception as exc:
        reason = _failure_reason("PDF_CONVERSION_FAILED", str(exc))
        complete_request_failure(db_path, request.id, ticker=ticker, error_reason=reason)
        logger.warning("request_failed", step="FAILED", error=reason)
        return ProcessResult(ticker=ticker, status="FAILED", destination_or_error=reason)

    with progress(ticker, "Persist result"):
        complete_request_success(
            db_path,
            request.id,
            FilingResponseData(
                ticker=metadata.ticker,
                cik=metadata.cik,
                company_name=metadata.company_name,
                form_type=metadata.form_type,
                filing_date=metadata.filing_date,
                accession_number=metadata.accession_number,
                source_url=metadata.source_url,
                pdf_path=str(pdf_path),
            ),
        )
    duration_ms = int((time.monotonic() - started_at) * 1000)
    logger.info(
        "request_completed",
        step="COMPLETED",
        duration_ms=duration_ms,
        pdf_path=str(pdf_path),
    )
    return ProcessResult(ticker=ticker, status="SUCCESS", destination_or_error=str(pdf_path))