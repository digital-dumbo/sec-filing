from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from k10fetcher.db import connect, transaction

REQUEST_PENDING = "PENDING"
REQUEST_PROCESSING = "PROCESSING"
REQUEST_COMPLETED = "COMPLETED"
REQUEST_FAILED = "FAILED"

RESPONSE_SUCCESS = "SUCCESS"
RESPONSE_FAILED = "FAILED"


@dataclass(frozen=True)
class CompanyDirectoryEntry:
    cik: str
    ticker: str
    company_name: str


@dataclass(frozen=True)
class FilingRequest:
    id: int
    batch_id: str
    raw_input: str
    normalized_ticker: str | None
    status: str
    error_reason: str | None


@dataclass(frozen=True)
class FilingResponseData:
    ticker: str | None
    cik: str | None
    company_name: str | None
    form_type: str
    filing_date: str | None
    accession_number: str | None
    source_url: str | None
    pdf_path: str | None


@dataclass(frozen=True)
class RecentFilingStatus:
    request_id: int
    batch_id: str
    ticker: str | None
    request_status: str
    response_status: str | None
    filing_date: str | None
    pdf_path: str | None
    error_reason: str | None
    requested_at: str
    completed_at: str | None


@dataclass(frozen=True)
class CachedFilingResponse:
    ticker: str
    cik: str
    company_name: str
    form_type: str
    filing_date: str
    accession_number: str
    source_url: str
    pdf_path: str


def _row_to_request(row: Any) -> FilingRequest:
    return FilingRequest(
        id=row["id"],
        batch_id=row["batch_id"],
        raw_input=row["raw_input"],
        normalized_ticker=row["normalized_ticker"],
        status=row["status"],
        error_reason=row["error_reason"],
    )


def new_batch_id() -> str:
    return uuid4().hex


def upsert_company_directory(db_path: Path, entries: list[CompanyDirectoryEntry]) -> int:
    if not entries:
        return 0

    with transaction(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO company_directory (cik, ticker, company_name, updated_at)
            VALUES (:cik, :ticker, :company_name, CURRENT_TIMESTAMP)
            ON CONFLICT(ticker) DO UPDATE SET
                cik = excluded.cik,
                company_name = excluded.company_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                {
                    "cik": entry.cik,
                    "ticker": entry.ticker.upper(),
                    "company_name": entry.company_name,
                }
                for entry in entries
            ],
        )
    return len(entries)


def find_company_by_ticker(db_path: Path, ticker: str) -> CompanyDirectoryEntry | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT cik, ticker, company_name
            FROM company_directory
            WHERE ticker = ?
            """,
            (ticker.upper(),),
        ).fetchone()

    if row is None:
        return None
    return CompanyDirectoryEntry(
        cik=row["cik"],
        ticker=row["ticker"],
        company_name=row["company_name"],
    )


def create_filing_requests(
    db_path: Path,
    raw_inputs: list[str],
    *,
    batch_id: str | None = None,
) -> tuple[str, list[FilingRequest]]:
    batch_id = batch_id or new_batch_id()
    requests: list[FilingRequest] = []

    with transaction(db_path) as connection:
        for raw_input in raw_inputs:
            normalized = raw_input.strip().upper() or None
            cursor = connection.execute(
                """
                INSERT INTO filing_requests (
                    batch_id, raw_input, normalized_ticker, status
                )
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, raw_input, normalized, REQUEST_PENDING),
            )
            request_id = cursor.lastrowid
            connection.execute(
                """
                INSERT INTO filing_processing_steps (request_id, step, message)
                VALUES (?, 'QUEUED', ?)
                """,
                (request_id, f"Queued {normalized or raw_input}"),
            )
            row = connection.execute(
                """
                SELECT id, batch_id, raw_input, normalized_ticker, status, error_reason
                FROM filing_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            requests.append(_row_to_request(row))

    return batch_id, requests


def start_processing_request(db_path: Path, request_id: int, *, step: str, message: str) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            UPDATE filing_requests
            SET status = ?, started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
            WHERE id = ?
            """,
            (REQUEST_PROCESSING, request_id),
        )
        connection.execute(
            """
            INSERT INTO filing_processing_steps (request_id, step, message)
            VALUES (?, ?, ?)
            """,
            (request_id, step, message),
        )


def record_processing_step(db_path: Path, request_id: int, *, step: str, message: str) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO filing_processing_steps (request_id, step, message)
            VALUES (?, ?, ?)
            """,
            (request_id, step, message),
        )


def complete_request_success(
    db_path: Path,
    request_id: int,
    response: FilingResponseData,
    *,
    step: str = "OUTPUT_WRITTEN",
    message: str = "Output row written successfully.",
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO filing_responses (
                request_id, ticker, cik, company_name, form_type, filing_date,
                accession_number, source_url, status, pdf_path, error_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(request_id) DO UPDATE SET
                ticker = excluded.ticker,
                cik = excluded.cik,
                company_name = excluded.company_name,
                form_type = excluded.form_type,
                filing_date = excluded.filing_date,
                accession_number = excluded.accession_number,
                source_url = excluded.source_url,
                status = excluded.status,
                pdf_path = excluded.pdf_path,
                error_reason = NULL,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                request_id,
                response.ticker,
                response.cik,
                response.company_name,
                response.form_type,
                response.filing_date,
                response.accession_number,
                response.source_url,
                RESPONSE_SUCCESS,
                response.pdf_path,
            ),
        )
        connection.execute(
            """
            INSERT INTO filing_processing_steps (request_id, step, message)
            VALUES (?, ?, ?)
            """,
            (request_id, step, message),
        )
        if step != "COMPLETED":
            connection.execute(
                """
                INSERT INTO filing_processing_steps (request_id, step, message)
                VALUES (?, 'COMPLETED', 'Request completed successfully.')
                """,
                (request_id,),
            )
        connection.execute(
            """
            UPDATE filing_requests
            SET status = ?, error_reason = NULL, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (REQUEST_COMPLETED, request_id),
        )


def complete_request_failure(
    db_path: Path,
    request_id: int,
    *,
    error_reason: str,
    ticker: str | None = None,
    step: str = "failed",
) -> None:
    with transaction(db_path) as connection:
        connection.execute(
            """
            INSERT INTO filing_responses (
                request_id, ticker, cik, company_name, form_type, filing_date,
                accession_number, source_url, status, pdf_path, error_reason
            )
            VALUES (?, ?, NULL, NULL, '10-K', NULL, NULL, NULL, ?, NULL, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                ticker = excluded.ticker,
                status = excluded.status,
                pdf_path = NULL,
                error_reason = excluded.error_reason,
                created_at = CURRENT_TIMESTAMP
            """,
            (request_id, ticker, RESPONSE_FAILED, error_reason),
        )
        connection.execute(
            """
            INSERT INTO filing_processing_steps (request_id, step, message)
            VALUES (?, ?, ?)
            """,
            (request_id, step, error_reason),
        )
        connection.execute(
            """
            UPDATE filing_requests
            SET status = ?, error_reason = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (REQUEST_FAILED, error_reason, request_id),
        )


def get_request(db_path: Path, request_id: int) -> FilingRequest | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, batch_id, raw_input, normalized_ticker, status, error_reason
            FROM filing_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()

    if row is None:
        return None
    return _row_to_request(row)


def count_rows(db_path: Path, table_name: str) -> int:
    allowed_tables = {
        "company_directory",
        "filing_requests",
        "filing_processing_steps",
        "filing_responses",
    }
    if table_name not in allowed_tables:
        raise ValueError(f"Unsupported table: {table_name}")

    with connect(db_path) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]


def list_recent_statuses(
    db_path: Path,
    *,
    limit: int = 20,
    batch_id: str | None = None,
) -> list[RecentFilingStatus]:
    if limit < 1:
        raise ValueError("limit must be greater than zero")

    where_clause = "WHERE requests.batch_id = ?" if batch_id else ""
    params: tuple[object, ...] = (batch_id, limit) if batch_id else (limit,)

    with connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT
                requests.id AS request_id,
                requests.batch_id,
                requests.normalized_ticker AS ticker,
                requests.status AS request_status,
                responses.status AS response_status,
                responses.filing_date,
                responses.pdf_path,
                COALESCE(responses.error_reason, requests.error_reason) AS error_reason,
                requests.requested_at,
                requests.completed_at
            FROM filing_requests AS requests
            LEFT JOIN filing_responses AS responses
                ON responses.request_id = requests.id
            {where_clause}
            ORDER BY requests.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

    return [
        RecentFilingStatus(
            request_id=row["request_id"],
            batch_id=row["batch_id"],
            ticker=row["ticker"],
            request_status=row["request_status"],
            response_status=row["response_status"],
            filing_date=row["filing_date"],
            pdf_path=row["pdf_path"],
            error_reason=row["error_reason"],
            requested_at=row["requested_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]


def find_latest_successful_response(
    db_path: Path,
    *,
    ticker: str,
    form_type: str = "10-K",
) -> CachedFilingResponse | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT ticker, cik, company_name, form_type, filing_date, accession_number,
                   source_url, pdf_path
            FROM filing_responses
            WHERE ticker = ?
              AND form_type = ?
              AND status = ?
              AND pdf_path IS NOT NULL
            ORDER BY filing_date DESC, id DESC
            LIMIT 1
            """,
            (ticker.upper(), form_type, RESPONSE_SUCCESS),
        ).fetchone()

    if row is None:
        return None
    return CachedFilingResponse(
        ticker=row["ticker"],
        cik=row["cik"],
        company_name=row["company_name"],
        form_type=row["form_type"],
        filing_date=row["filing_date"],
        accession_number=row["accession_number"],
        source_url=row["source_url"],
        pdf_path=row["pdf_path"],
    )
