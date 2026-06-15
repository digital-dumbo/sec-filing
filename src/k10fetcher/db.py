import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company_directory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cik TEXT NOT NULL,
    ticker TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_company_directory_cik
ON company_directory (cik);

CREATE TABLE IF NOT EXISTS filing_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    normalized_ticker TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')
    ),
    error_reason TEXT,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_filing_requests_batch
ON filing_requests (batch_id);

CREATE INDEX IF NOT EXISTS idx_filing_requests_status
ON filing_requests (status);

CREATE TABLE IF NOT EXISTS filing_processing_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    step TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES filing_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_processing_steps_request
ON filing_processing_steps (request_id);

CREATE TABLE IF NOT EXISTS filing_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL UNIQUE,
    ticker TEXT,
    cik TEXT,
    company_name TEXT,
    form_type TEXT NOT NULL DEFAULT '10-K',
    filing_date TEXT,
    accession_number TEXT,
    source_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    pdf_path TEXT,
    error_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES filing_requests(id)
);

CREATE INDEX IF NOT EXISTS idx_filing_responses_ticker
ON filing_responses (ticker);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path) -> None:
    with connect(db_path) as connection:
        connection.executescript(SCHEMA_SQL)


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    try:
        connection.execute("BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()