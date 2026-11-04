"""Deterministic input/output guardrails for model-facing endpoints.

These checks are pure Python: no database, no network, no model calls. They
complement (never replace) the prompt-embedded untrusted-document boundary in
the structured extractor and the log-redaction policy in ``logging_config``.

Privacy: verdicts carry pattern names, counts and lengths only. They never echo
matched secrets, credentials, or user content, so they are safe to persist in
audit metadata and Langfuse trace attributes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

MAX_QUESTION_CHARS = 2000
MIN_QUESTION_CHARS = 3

# Suspicious instructions smuggled into a user question or document-scoped query.
_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ignore_instructions", r"ignor(e|ing)\s+(all\s+)?(prior|previous|above|earlier)\s+instructions?"),
    ("system_prompt_reveal", r"(reveal|show|print|output|disclose)\s+(your\s+)?(system\s+prompt|instructions|prompt)"),
    ("role_override", r"(you\s+are\s+now|act\s+as|pretend\s+(to\s+be|you\s+are)|new\s+persona|jailbreak|dan\s+mode)"),
    ("tool_misuse", r"(execute|run|eval(uate)?)\s+(this\s+)?(code|command|sql|query|script)"),
    ("credential_request", r"(api[\s_-]?key|secret[\s_-]?key|password|private[\s_-]?key|service[\s_-]?role)"),
    ("instruction_delimiter", r"(```\s*system|<\s*system\s*>|\[system\]|###\s*instruction)"),
)

# Secret-like shapes that must never appear in a model answer surfaced to users.
_SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}"),
    ("api_key_assignment", r"(?i)(api[\s_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9\-._~+/]{16,}"),
    ("aws_key", r"AKIA[0-9A-Z]{16}"),
    ("jwt_like", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

_COMPILED_INJECTION = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _INJECTION_PATTERNS)
_COMPILED_SECRET = tuple((name, re.compile(pattern)) for name, pattern in _SECRET_PATTERNS)


@dataclass(frozen=True)
class InputVerdict:
    """Outcome of screening caller-supplied text before any model call."""

    passed: bool
    flags: tuple[str, ...] = ()
    reason: str | None = None
    char_count: int = 0

    def trace_fields(self) -> dict[str, object]:
        return {
            "guardrail_input_passed": self.passed,
            "guardrail_input_flags": ",".join(self.flags) if self.flags else "none",
            "question_length": self.char_count,
        }


@dataclass(frozen=True)
class OutputVerdict:
    """Outcome of screening model-produced text before persistence/display."""

    passed: bool
    flags: tuple[str, ...] = ()
    reason: str | None = None
    checked_claims: int = 0
    unsupported_claims: int = 0
    unknown_citations: int = 0

    def trace_fields(self) -> dict[str, object]:
        return {
            "guardrail_output_passed": self.passed,
            "guardrail_output_flags": ",".join(self.flags) if self.flags else "none",
            "unsupported_claims": self.unsupported_claims,
            "unknown_citations": self.unknown_citations,
        }


def check_user_input(text: str | None) -> InputVerdict:
    """Screen a caller-supplied question. Fails closed on injection shapes."""
    normalized = unicodedata.normalize("NFC", text or "").strip()
    collapsed = re.sub(r"\s+", " ", normalized)
    char_count = len(collapsed)
    if char_count < MIN_QUESTION_CHARS:
        return InputVerdict(passed=False, flags=("too_short",), reason="question_too_short", char_count=char_count)
    if char_count > MAX_QUESTION_CHARS:
        return InputVerdict(passed=False, flags=("too_long",), reason="question_too_long", char_count=char_count)
    flags = [name for name, pattern in _COMPILED_INJECTION if pattern.search(collapsed)]
    if flags:
        return InputVerdict(
            passed=False,
            flags=tuple(flags),
            reason="prompt_injection_suspected",
            char_count=char_count,
        )
    return InputVerdict(passed=True, char_count=char_count)


def check_model_answer(
    answer_text: str | None,
    cited_chunk_ids: set[str],
    retrieved_chunk_ids: set[str],
    claim_count: int,
) -> OutputVerdict:
    """Screen a model answer for secret leaks and citation integrity (FR-013).

    Citations outside the retrieved set are counted (never echoed) so the caller
    can mark the affected claims unsupported instead of presenting them as fact.
    """
    normalized = unicodedata.normalize("NFC", answer_text or "")
    flags = [name for name, pattern in _COMPILED_SECRET if pattern.search(normalized)]
    unknown = cited_chunk_ids - retrieved_chunk_ids
    if unknown:
        flags.append("unknown_citation")
    passed = not flags
    reason = None if passed else ("secret_leak_suspected" if any(name != "unknown_citation" for name in flags) else "unknown_citation")
    return OutputVerdict(
        passed=passed,
        flags=tuple(flags),
        reason=reason,
        checked_claims=max(claim_count, 0),
        unknown_citations=len(unknown),
    )


def audit_metadata(*verdicts: InputVerdict | OutputVerdict) -> dict[str, object]:
    """Merge verdict trace fields for audit events. Content-free by construction."""
    merged: dict[str, object] = {}
    for verdict in verdicts:
        merged.update(verdict.trace_fields())
    return merged


@dataclass(frozen=True)
class QuoteCheck:
    quote_chars: int
    matched: bool


def check_quote_grounding(quote: str | None, chunk_text: str | None) -> QuoteCheck:
    """NFC + whitespace-collapse exact-substring check, mirroring evidence rules."""
    normalized_quote = re.sub(r"\s+", " ", unicodedata.normalize("NFC", quote or "")).strip()
    normalized_chunk = re.sub(r"\s+", " ", unicodedata.normalize("NFC", chunk_text or "")).strip()
    if not normalized_quote or not normalized_chunk:
        return QuoteCheck(quote_chars=len(normalized_quote), matched=False)
    return QuoteCheck(quote_chars=len(normalized_quote), matched=normalized_quote in normalized_chunk)


@dataclass(frozen=True)
class GuardrailReport:
    """Combined report for endpoints that run several checks."""

    passed: bool
    flags: tuple[str, ...] = field(default_factory=tuple)
