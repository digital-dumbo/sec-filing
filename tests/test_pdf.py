from pathlib import Path

import pytest

import k10fetcher.pdf as pdf_module
from k10fetcher.pdf import build_pdf_path, convert_html_to_pdf_atomic
from k10fetcher.sec_client import FilingMetadata


class FakeHTML:
    calls: list[tuple[str, str | None, Path]] = []

    def __init__(self, *, string: str, base_url: str | None = None):
        self.string = string
        self.base_url = base_url

    def write_pdf(self, target: Path) -> None:
        self.calls.append((self.string, self.base_url, target))
        target.write_bytes(b"%PDF fake")


class FailingHTML(FakeHTML):
    def write_pdf(self, target: Path) -> None:
        target.write_bytes(b"partial")
        raise RuntimeError("conversion failed")


def _metadata() -> FilingMetadata:
    return FilingMetadata(
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        form_type="10-K",
        filing_date="2025-10-31",
        accession_number="0000320193-25-000079",
        primary_document="aapl-20250927.htm",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/example.htm",
    )


def test_build_pdf_path_uses_active_ticker_form_directory(tmp_path: Path):
    assert build_pdf_path(tmp_path, _metadata()) == (
        tmp_path / "active" / "AAPL" / "10-K" / "AAPL_10-K_2025-10-31.pdf"
    )


def test_convert_html_to_pdf_atomic_writes_temp_then_renames(tmp_path: Path, monkeypatch):
    FakeHTML.calls = []
    monkeypatch.setattr(pdf_module, "HTML", FakeHTML)
    target = tmp_path / "active" / "AAPL" / "10-K" / "AAPL_10-K_2025-10-31.pdf"

    result = convert_html_to_pdf_atomic(
        "<html><body>Hello</body></html>",
        target,
        base_url="https://www.sec.gov/Archives/example.htm",
    )

    assert result == target
    assert target.read_bytes() == b"%PDF fake"
    assert not target.with_name(f"{target.name}.tmp").exists()
    assert FakeHTML.calls == [
        (
            "<html><body>Hello</body></html>",
            "https://www.sec.gov/Archives/example.htm",
            target.with_name(f"{target.name}.tmp"),
        )
    ]


def test_convert_html_to_pdf_atomic_removes_temp_on_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pdf_module, "HTML", FailingHTML)
    target = tmp_path / "AAPL_10-K_2025-10-31.pdf"

    with pytest.raises(RuntimeError, match="conversion failed"):
        convert_html_to_pdf_atomic("<html>bad</html>", target)

    assert not target.exists()
    assert not target.with_name(f"{target.name}.tmp").exists()