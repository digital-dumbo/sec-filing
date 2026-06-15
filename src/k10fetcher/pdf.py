from __future__ import annotations

from pathlib import Path

from weasyprint import HTML

from k10fetcher.sec_client import FilingMetadata


def build_pdf_path(data_dir: Path, metadata: FilingMetadata) -> Path:
    filename = f"{metadata.ticker}_{metadata.form_type}_{metadata.filing_date}.pdf"
    return data_dir / "active" / metadata.ticker / metadata.form_type / filename


def convert_html_to_pdf_atomic(html: str, target_pdf: Path, *, base_url: str | None = None) -> Path:
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    temp_pdf = target_pdf.with_name(f"{target_pdf.name}.tmp")
    if temp_pdf.exists():
        temp_pdf.unlink()

    try:
        HTML(string=html, base_url=base_url).write_pdf(temp_pdf)
        temp_pdf.replace(target_pdf)
    except Exception:
        if temp_pdf.exists():
            temp_pdf.unlink()
        raise

    return target_pdf
