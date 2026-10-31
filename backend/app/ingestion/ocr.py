"""
OCR service using PyMuPDF's Tesseract integration.

Supports configurable languages (default: eng+mal), configurable DPI (default: 300),
per-page memory isolation, language availability checks, and structured error handling.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pymupdf

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Base exception for OCR operations."""


class TesseractNotFoundError(OCRError):
    """Raised when Tesseract OCR binary or library is not available."""


class TesseractLanguageNotFoundError(OCRError):
    """Raised when configured OCR languages are missing from tessdata."""


@dataclass
class PageOCRResult:
    """OCR result for a single page."""

    page_number: int  # 1-based
    recognized_text: str
    char_count: int
    extraction_method: str  # "OCR", "META_VISION", "MIXED"
    extraction_status: str  # "COMPLETED" or "FAILED"
    languages_used: str
    provider: str = "tesseract"
    metadata: dict = field(default_factory=dict)
    error_message: str | None = None
    uncertain_regions: list[dict] = field(default_factory=list)
    latency_ms: int | None = None
    model_id: str | None = None
    input_hash: str | None = None


def check_tesseract_installed() -> bool:
    """Verify that tesseract CLI / library is accessible."""
    return shutil.which("tesseract") is not None


def get_available_languages(tessdata_prefix: str | None = None) -> list[str]:
    """Retrieve list of installed Tesseract languages."""
    env = os.environ.copy()
    if tessdata_prefix:
        env["TESSDATA_PREFIX"] = tessdata_prefix

    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            return []
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        # Skip header line like 'List of available languages in "...":'
        langs = [line for line in lines if not line.startswith("List of available")]
        return langs
    except Exception:
        return []


def validate_ocr_languages(languages: str, tessdata_prefix: str | None = None) -> list[str]:
    """
    Validate that all requested '+' delimited language codes are installed.
    Raises TesseractNotFoundError or TesseractLanguageNotFoundError if invalid.
    """
    if not check_tesseract_installed():
        raise TesseractNotFoundError("Tesseract OCR is not installed or not in PATH")

    available = get_available_languages(tessdata_prefix)
    if not available:
        logger.warning(
            "tesseract_no_langs_detected",
            extra={"event": "ocr.no_languages_detected"},
        )

    requested_codes = [lang.strip() for lang in languages.split("+") if lang.strip()]
    if not requested_codes:
        raise TesseractLanguageNotFoundError("No OCR languages specified")

    if available:
        missing = [code for code in requested_codes if code not in available]
        if missing:
            raise TesseractLanguageNotFoundError(
                f"Tesseract language data missing for: {', '.join(missing)}. "
                f"Available languages: {', '.join(available[:10])}..."
            )

    return requested_codes


def run_ocr_on_page(
    doc: pymupdf.Document,
    page_number: int,  # 1-based
    *,
    languages: str = "eng+mal",
    dpi: int = 300,
    existing_digital_text: str | None = None,
    tessdata_prefix: str | None = None,
) -> PageOCRResult:
    """
    Execute OCR on a single 1-based page of an open PyMuPDF document.
    Never renders all pages in memory simultaneously.
    """
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix

    page_idx = page_number - 1
    if page_idx < 0 or page_idx >= len(doc):
        raise ValueError(f"Page number {page_number} out of range [1, {len(doc)}]")

    page = doc[page_idx]

    try:
        textpage = page.get_textpage_ocr(
            language=languages,
            dpi=dpi,
            tessdata=tessdata_prefix,
            full=True,
        )
        ocr_text = (textpage.extractText() or "").strip()
    except Exception as exc:
        err_msg = str(exc)
        logger.warning(
            "ocr_page_failed",
            extra={
                "event": "ocr.page_failed",
                "page_number": page_number,
                "error": err_msg,
            },
        )
        if "Tesseract language initialisation failed" in err_msg or "Failed loading language" in err_msg:
            raise TesseractLanguageNotFoundError(f"OCR language init failed for '{languages}': {err_msg}") from exc
        return PageOCRResult(
            page_number=page_number,
            recognized_text="",
            char_count=0,
            extraction_method="OCR",
            extraction_status="FAILED",
            languages_used=languages,
            metadata={"dpi": dpi, "error": err_msg},
            error_message=err_msg,
        )

    has_digital = bool(existing_digital_text and existing_digital_text.strip())
    has_ocr = bool(ocr_text)

    # Determine combined text & method
    if has_digital and has_ocr:
        # Preserve digital text and append OCR text if different, without debug labels
        if ocr_text in existing_digital_text:
            combined_text = existing_digital_text
        else:
            combined_text = f"{existing_digital_text}\n\n{ocr_text}"
        method = "MIXED"
    elif has_ocr:
        combined_text = ocr_text
        method = "OCR"
    elif has_digital:
        combined_text = existing_digital_text
        method = "DIGITAL_TEXT"
    else:
        combined_text = ""
        method = "OCR"

    status = "COMPLETED" if bool(combined_text) else "FAILED"

    return PageOCRResult(
        page_number=page_number,
        recognized_text=combined_text,
        char_count=len(combined_text),
        extraction_method=method,
        extraction_status=status,
        languages_used=languages,
        provider="tesseract",
        metadata={
            "dpi": dpi,
            "ocr_chars": len(ocr_text),
            "digital_chars": len(existing_digital_text or ""),
            "processed_at": datetime.now(UTC).isoformat(),
        },
        error_message=None if status == "COMPLETED" else "No text recognized by OCR",
    )


def perform_document_ocr(
    pdf_bytes: bytes,
    pages_to_ocr: list[int],
    *,
    languages: str = "eng+mal",
    dpi: int = 300,
    existing_pages: dict[int, str] | None = None,
    tessdata_prefix: str | None = None,
) -> list[PageOCRResult]:
    """
    Run OCR on selected 1-based page numbers of a document provided as raw bytes.
    Validates PDF, opens it safely, processes pages one by one, and cleans up memory.
    """
    validate_ocr_languages(languages, tessdata_prefix)

    try:
        doc = pymupdf.open("pdf", pdf_bytes)
    except Exception as exc:
        raise OCRError(f"Failed to open PDF for OCR: {exc}") from exc

    results: list[PageOCRResult] = []
    existing_pages = existing_pages or {}

    try:
        for page_num in pages_to_ocr:
            existing_text = existing_pages.get(page_num)
            res = run_ocr_on_page(
                doc,
                page_num,
                languages=languages,
                dpi=dpi,
                existing_digital_text=existing_text,
                tessdata_prefix=tessdata_prefix,
            )
            results.append(res)
    finally:
        doc.close()

    return results


def render_page_to_image_bytes(
    doc: pymupdf.Document,
    page_number: int,
    dpi: int = 250,
    max_dim: int = 2048,
) -> tuple[bytes, str]:
    """
    Render a single page to size-controlled PNG image bytes.
    Clamps max dimension to avoid unbounded memory / payload sizes.
    """
    page_idx = page_number - 1
    page = doc[page_idx]

    # Calculate scale factor
    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    # If exceeding max_dim, scale down proportionally
    if max(pix.width, pix.height) > max_dim:
        scale = max_dim / float(max(pix.width, pix.height))
        mat = pymupdf.Matrix(zoom * scale, zoom * scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)

    png_bytes = pix.tobytes("png")
    return png_bytes, "image/png"


