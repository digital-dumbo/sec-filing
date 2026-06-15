from pathlib import Path
from typing import Any

import structlog

from k10fetcher.config import settings


class _LineBufferedLogFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, message: str) -> None:
        with self.path.open("a", encoding="utf-8") as file:
            file.write(message)

    def flush(self) -> None:
        return None


def configure_logging(log_path: Path | None = None) -> Path:
    resolved_path = log_path or settings.log_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=_LineBufferedLogFile(resolved_path)),
    )
    return resolved_path


def get_logger(**context: Any) -> structlog.BoundLogger:
    return structlog.get_logger().bind(**context)