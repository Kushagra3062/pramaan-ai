"""
Meta Muse Spark Vision extraction provider and prompt design.
Handles image preparation, base64 encoding, versioned prompts,
structured response parsing, and error normalization.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.meta_client import get_meta_client

logger = logging.getLogger(__name__)

# Prompt version identifier
MUSE_SPARK_TRANSCRIPTION_PROMPT_VERSION = "v1.1-vision-transcription"

MUSE_SPARK_SYSTEM_INSTRUCTION = """You are a highly accurate, faithful document transcription engine for operational documents.
Your goal is to transcribe all text visible on the document page image with 100% precision.

RULES:
1. Transcribe visible text faithfully as written.
2. PRESERVE ORIGINAL SCRIPT AND LANGUAGE: Do NOT translate. Keep Malayalam script in Malayalam Unicode, and English in English Latin characters.
3. Preserve numbers, dates, circular identifiers, amounts, punctuation, and currencies exactly.
4. Do NOT join separate words together. Ensure accurate word boundaries.
5. Preserve paragraph structure, line breaks, section headers, bullet numbers, and table alignments wherever possible.
6. Do NOT invent, assume, summarize, or extrapolate any text not present in the image.
7. Mark unreadable or blurry regions explicitly under "uncertain_regions".
8. Return your output STRICTLY as valid JSON matching the following schema:
{
  "transcription": "Complete faithful transcribed text here...",
  "language_hints": ["English", "Malayalam"],
  "uncertain_regions": [
    {"text_excerpt": "blurry fragment", "reason": "ink blot / low resolution"}
  ]
}
"""


class UncertainRegion(BaseModel):
    text_excerpt: str = ""
    reason: str = ""


class VisionTranscriptionPayload(BaseModel):
    transcription: str = Field(description="The complete faithful text transcribed from the image.")
    language_hints: list[str] = Field(default_factory=list, description="Languages detected on the page.")
    uncertain_regions: list[UncertainRegion] = Field(default_factory=list, description="Uncertain or illegible text segments.")


@dataclass
class MetaVisionResult:
    transcription: str
    language_hints: list[str]
    uncertain_regions: list[dict]
    model_id: str
    prompt_version: str
    input_hash: str
    latency_ms: int
    usage_metadata: dict = field(default_factory=dict)
    status: str = "COMPLETED"
    error_code: str | None = None
    error_message: str | None = None


class MetaVisionExtractor:
    """Dedicated extractor for scanned document pages using Meta Muse Spark."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_id = settings.meta_model_id
        self.prompt_version = MUSE_SPARK_TRANSCRIPTION_PROMPT_VERSION

    def transcribe_image_bytes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
    ) -> MetaVisionResult:
        """
        Send a rendered page image to muse-spark-1.3 for transcription.
        Image bytes are converted to base64 data URL. Never logs sensitive content.
        """
        input_hash = hashlib.sha256(image_bytes).hexdigest()
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_data}"

        client = get_meta_client()
        started = time.perf_counter()

        try:
            # We use chat completions with multimodal image_url
            response = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": MUSE_SPARK_SYSTEM_INSTRUCTION},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Please transcribe all text visible in this document page image faithfully following the instructions. Return strictly JSON.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                            },
                        ],
                    },
                ],
                max_tokens=max(self.settings.meta_max_output_tokens, 3000),
                temperature=0.0,
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000)

            choice = response.choices[0]
            raw_text = choice.message.content or ""
            usage_dict = response.usage.model_dump() if response.usage else {}

            parsed_payload = self._parse_structured_response(raw_text)

            logger.info(
                "meta_vision_transcription_success",
                extra={
                    "event": "meta_vision.completed",
                    "model": self.model_id,
                    "duration_ms": elapsed_ms,
                    "input_hash": input_hash,
                    "char_count": len(parsed_payload.transcription),
                },
            )

            return MetaVisionResult(
                transcription=parsed_payload.transcription,
                language_hints=parsed_payload.language_hints,
                uncertain_regions=[u.model_dump() for u in parsed_payload.uncertain_regions],
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                latency_ms=elapsed_ms,
                usage_metadata=usage_dict,
                status="COMPLETED",
            )

        except (RateLimitError, APIConnectionError, APITimeoutError, APIStatusError) as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            err_code = type(exc).__name__
            logger.error(
                "meta_vision_transcription_error",
                extra={
                    "event": "meta_vision.failed",
                    "error_type": err_code,
                    "duration_ms": elapsed_ms,
                },
            )
            return MetaVisionResult(
                transcription="",
                language_hints=[],
                uncertain_regions=[],
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                latency_ms=elapsed_ms,
                status="FAILED",
                error_code=err_code,
                error_message=str(exc),
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            logger.exception(
                "meta_vision_unexpected_error",
                extra={"event": "meta_vision.unexpected_failure", "duration_ms": elapsed_ms},
            )
            return MetaVisionResult(
                transcription="",
                language_hints=[],
                uncertain_regions=[],
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                input_hash=input_hash,
                latency_ms=elapsed_ms,
                status="FAILED",
                error_code="UnexpectedError",
                error_message=str(exc),
            )

    def _parse_structured_response(self, raw_text: str) -> VisionTranscriptionPayload:
        """Parse raw model output into structured payload with fallbacks."""
        text = raw_text.strip()
        # Clean markdown code fences if wrapped in ```json ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            return VisionTranscriptionPayload.model_validate(data)
        except Exception:
            # Fallback: if model returned direct text rather than json
            return VisionTranscriptionPayload(
                transcription=raw_text.strip(),
                language_hints=["English"],
                uncertain_regions=[],
            )

