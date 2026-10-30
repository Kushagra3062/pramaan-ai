"""Synthetic PDF generator for automated tests."""

from __future__ import annotations

import pymupdf as fitz


def create_synthetic_pdf(
    pages_text: list[str],
) -> bytes:
    """
    Generate a simple valid PDF in memory with the given text on each page.
    """
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)  # A4 standard points
        if text:
            # Insert text at top margin
            page.insert_text((50, 72), text, fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_empty_pdf() -> bytes:
    """Create a valid PDF structure with 0 pages."""
    doc = fitz.open()
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes

