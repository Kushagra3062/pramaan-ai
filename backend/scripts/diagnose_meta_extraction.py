"""Opt-in single-request Muse Spark extraction diagnostic (metadata only).

Makes EXACTLY ONE structured-extraction request against a trusted canonical chunk loaded from
PostgreSQL and prints safe metadata. It never prints the chunk text, prompt, model response,
extracted values or credentials, and it persists nothing (no facts, ModelUsage or audit rows).

Safety gates (BOTH required, otherwise it exits before any DB or network access):
  1. the command-line flag ``--execute``
  2. the environment variable ``RUN_META_DIAGNOSTIC=true``

Run from backend/ with the repo-root .env, for example:
  RUN_META_DIAGNOSTIC=true python scripts/diagnose_meta_extraction.py --execute \\
      --document-id <uuid> --page-number 2 --chunk-id <uuid>

It uses the existing StructuredExtractor with its current request configuration. The text is
never accepted from the command line: it is loaded from the database and must belong to the
given document, page and the document's own workspace.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow running `python scripts/diagnose_meta_extraction.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session  # noqa: E402

from app.db.repositories.document_repo import (  # noqa: E402
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
)
from app.ingestion.chunking import clean_canonical_text  # noqa: E402
from app.ingestion.structured_extractor import (  # noqa: E402
    ExtractionJSONParseError,
    ExtractionValidationError,
    StructuredExtractionError,
    StructuredExtractor,
)

ENV_GATE = "RUN_META_DIAGNOSTIC"
EXIT_REFUSED = 2
EXIT_INVALID_INPUT = 3
EXIT_REQUEST_FAILED = 1


class DiagnosticInputError(ValueError):
    """The requested document/page/chunk is not a valid canonical extraction input."""


@dataclass(frozen=True)
class TrustedChunk:
    document_id: uuid.UUID
    page_number: int
    chunk_id: uuid.UUID
    text: str  # never printed

    @property
    def char_count(self) -> int:
        return len(self.text)


def gates_open(execute_flag: bool, env: Mapping[str, str]) -> bool:
    """Both the --execute flag and RUN_META_DIAGNOSTIC=true are required."""
    return bool(execute_flag) and env.get(ENV_GATE, "").strip().lower() == "true"


def load_trusted_chunk(
    session: Session, document_id: uuid.UUID, page_number: int, chunk_id: uuid.UUID
) -> TrustedChunk:
    """Load and validate canonical provenance. Read-only; raises DiagnosticInputError."""
    document = DocumentRepository(session).get_by_id(document_id)
    if document is None or document.deleted_at is not None:
        raise DiagnosticInputError("document not found")

    page = DocumentPageRepository(session).get_by_page_number(document.id, page_number)
    if page is None:
        raise DiagnosticInputError("page not found for this document")
    if page.extraction_status != "COMPLETED":
        raise DiagnosticInputError("page is not COMPLETED (not canonical)")
    if not clean_canonical_text(page.extracted_text):
        raise DiagnosticInputError("page has no canonical text")

    chunk = DocumentChunkRepository(session).get_by_id(chunk_id)
    if chunk is None:
        raise DiagnosticInputError("chunk not found")
    if chunk.document_id != document.id:
        raise DiagnosticInputError("chunk does not belong to this document")
    if chunk.workspace_id != document.workspace_id:
        raise DiagnosticInputError("chunk does not belong to the document's workspace")
    if not (chunk.page_start == chunk.page_end == page_number):
        raise DiagnosticInputError("chunk is not a single-page chunk of the requested page")
    if not chunk.content or not chunk.content.strip():
        raise DiagnosticInputError("chunk content is empty")
    return TrustedChunk(document.id, page_number, chunk.id, chunk.content)


def run_single_request(chunk: TrustedChunk, extractor: Any) -> dict[str, Any]:
    """Make exactly one extraction call and return printable, content-free fields."""
    report: dict[str, Any] = {
        "document_id": str(chunk.document_id),
        "page_number": chunk.page_number,
        "chunk_id": str(chunk.chunk_id),
        "chunk_chars": chunk.char_count,
        "model_id": getattr(extractor, "model_id", None),
        "prompt_version": getattr(extractor, "prompt_version", None),
        "json_parsed": "not_attempted",
        "error_category": None,
    }
    diagnostics = None
    schema_errors = None
    schema_failure = False
    try:
        result = extractor.extract_from_text(chunk.text)  # the single request; no retries
        diagnostics = result.diagnostics
        report["json_parsed"] = True
    except StructuredExtractionError as exc:
        diagnostics = exc.diagnostics
        report["error_category"] = exc.category
        if isinstance(exc, ExtractionJSONParseError):
            report["json_parsed"] = False
        elif isinstance(exc, ExtractionValidationError):
            report["json_parsed"] = True  # valid JSON, wrong structure
            schema_failure = True
            schema_errors = getattr(exc, "schema_errors", None)
    except Exception:  # noqa: BLE001 - never echo messages (could carry sensitive detail)
        report["error_category"] = "UNEXPECTED_ERROR"

    report.update(
        request_id=getattr(diagnostics, "request_id", None),
        response_model=getattr(diagnostics, "response_model", None),
        finish_reason=getattr(diagnostics, "finish_reason", None),
        input_tokens=getattr(diagnostics, "input_tokens", None),
        output_tokens=getattr(diagnostics, "output_tokens", None),
        reasoning_tokens=getattr(diagnostics, "reasoning_tokens", None),
        content_length=getattr(diagnostics, "content_length", None),
        refusal_present=getattr(diagnostics, "refusal_present", None),
        tool_calls_count=getattr(diagnostics, "tool_calls_count", None),
    )
    if schema_failure:
        # Sanitized only: count, field locations and Pydantic error types. Never values or messages.
        report["validation_error_count"] = getattr(schema_errors, "error_count", None)
        report["validation_errors"] = (
            [f"{e.path} ({e.type})" for e in schema_errors.errors] if schema_errors is not None else None
        )
        report["validation_errors_truncated"] = getattr(schema_errors, "truncated_error_list", None)
    return report


def format_report(report: Mapping[str, Any]) -> str:
    lines = []
    for key, value in report.items():
        if key == "validation_errors":
            if value is None:
                lines.append("validation_errors: unknown")
            else:
                lines.append(f"validation_errors: {len(value)} listed")
                lines.extend(f"  - {item}" for item in value)
            continue
        shown = "unknown" if value is None and key != "error_category" else ("none" if value is None else value)
        lines.append(f"{key}: {shown}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in single Muse Spark extraction diagnostic (metadata only).")
    parser.add_argument("--execute", action="store_true", help=f"required, together with {ENV_GATE}=true")
    parser.add_argument("--document-id", type=uuid.UUID, required=True)
    parser.add_argument("--page-number", type=int, required=True)
    parser.add_argument("--chunk-id", type=uuid.UUID, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    session_factory: Callable[[], Session] | None = None,
    extractor_factory: Callable[[], Any] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if env is None else env
    if not gates_open(args.execute, environment):
        print(
            f"Refusing to run: both --execute and {ENV_GATE}=true are required. "
            "No database or network access was made."
        )
        return EXIT_REFUSED

    if session_factory is None:
        from app.core.logging_config import configure_logging
        from app.db.engine import SessionLocal

        configure_logging()
        session_factory = SessionLocal

    session = session_factory()
    try:
        try:
            chunk = load_trusted_chunk(session, args.document_id, args.page_number, args.chunk_id)
        except DiagnosticInputError as exc:
            print(f"Invalid diagnostic input: {exc}. No model request was made.")
            return EXIT_INVALID_INPUT
    finally:
        session.close()  # read-only; nothing to commit

    extractor = extractor_factory() if extractor_factory else StructuredExtractor()
    report = run_single_request(chunk, extractor)
    print(format_report(report))
    return 0 if report["error_category"] is None else EXIT_REQUEST_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
