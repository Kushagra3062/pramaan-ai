"""
PDF text extraction using PyMuPDF.

Extracts text from each page separately, preserving 1-based page numbering.
Detects pages with insufficient text that require OCR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pymupdf as fitz

logger = logging.getLogger(__name__)

# Minimum character count to consider a page as having meaningful text.
# Pages below this threshold are marked as needing OCR.
DEFAULT_MIN_TEXT_CHARS = 30

# PDF magic bytes: %PDF
PDF_MAGIC = b"%PDF"


@dataclass
class PageExtractionResult:
    """Result of extracting text from a single page."""

    page_number: int  # 1-based
    text: str
    char_count: int
    extraction_method: str  # "native_pdf" or "needs_ocr"
    extraction_status: str  # "COMPLETED" or "NEEDS_OCR"
    metadata: dict = field(default_factory=dict)


@dataclass
class PDFExtractionResult:
    """Result of extracting text from an entire PDF document."""

    page_count: int
    pages: list[PageExtractionResult]
    needs_ocr_pages: list[int]  # 1-based page numbers that need OCR
    overall_status: str  # "COMPLETED", "NEEDS_OCR", or "FAILED"
    error: str | None = None


class PDFExtractionError(Exception):
    """Raised when PDF extraction fails."""


def validate_pdf_signature(data: bytes) -> bool:
    """Check that *data* starts with the PDF magic bytes."""
    return data[:4] == PDF_MAGIC


def extract_pdf(
    data: bytes,
    *,
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
) -> PDFExtractionResult:
    """
    Extract text from every page of a PDF document.

    Parameters
    ----------
    data:
        Raw PDF bytes.
    min_text_chars:
        Minimum character count for a page to be considered as having
        meaningful text.  Pages below this threshold are marked
        ``NEEDS_OCR``.

    Returns
    -------
    PDFExtractionResult
        Structured result with per-page text and metadata.

    Raises
    ------
    PDFExtractionError
        If the PDF cannot be opened (corrupt, encrypted, etc.).
    """
    if not validate_pdf_signature(data):
        raise PDFExtractionError("File does not have a valid PDF signature")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PDFExtractionError(f"Cannot open PDF: {exc}") from exc

    if doc.is_encrypted:
        doc.close()
        raise PDFExtractionError("PDF is encrypted and cannot be processed")

    page_count = len(doc)
    if page_count == 0:
        doc.close()
        return PDFExtractionResult(
            page_count=0,
            pages=[],
            needs_ocr_pages=[],
            overall_status="FAILED",
            error="PDF has zero pages",
        )

    pages: list[PageExtractionResult] = []
    needs_ocr_pages: list[int] = []

    for i in range(page_count):
        page_number = i + 1  # 1-based
        try:
            page = doc[i]
            text = page.get_text("text") or ""
            text = text.strip()
            char_count = len(text)

            page_rect = page.rect
            page_meta = {
                "width": round(page_rect.width, 2),
                "height": round(page_rect.height, 2),
                "char_count": char_count,
            }

            if char_count < min_text_chars:
                # Not enough text — likely a scanned/image page
                needs_ocr_pages.append(page_number)
                pages.append(
                    PageExtractionResult(
                        page_number=page_number,
                        text=text,
                        char_count=char_count,
                        extraction_method="needs_ocr",
                        extraction_status="NEEDS_OCR",
                        metadata=page_meta,
                    )
                )
            else:
                pages.append(
                    PageExtractionResult(
                        page_number=page_number,
                        text=text,
                        char_count=char_count,
                        extraction_method="native_pdf",
                        extraction_status="COMPLETED",
                        metadata=page_meta,
                    )
                )
        except Exception:
            logger.exception(
                "page_extraction_failed",
                extra={"event": "extraction.page_failed", "page_number": page_number},
            )
            needs_ocr_pages.append(page_number)
            pages.append(
                PageExtractionResult(
                    page_number=page_number,
                    text="",
                    char_count=0,
                    extraction_method="needs_ocr",
                    extraction_status="NEEDS_OCR",
                    metadata={"error": "extraction_failed"},
                )
            )

    doc.close()

    # Determine overall status
    if len(needs_ocr_pages) == page_count:
        # ALL pages need OCR — no meaningful text was extracted
        overall_status = "NEEDS_OCR"
    elif needs_ocr_pages:
        # Some pages have text, some need OCR — partial extraction
        overall_status = "NEEDS_OCR"
    else:
        overall_status = "COMPLETED"

    logger.info(
        "pdf_extraction_completed",
        extra={
            "event": "extraction.completed",
            "page_count": page_count,
            "needs_ocr_pages": len(needs_ocr_pages),
            "overall_status": overall_status,
        },
    )
    return PDFExtractionResult(
        page_count=page_count,
        pages=pages,
        needs_ocr_pages=needs_ocr_pages,
        overall_status=overall_status,
    )

