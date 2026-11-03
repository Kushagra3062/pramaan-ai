"""
Pydantic v2 schemas for structured operational fact and requirement extraction.

Defines the strongly typed candidate extraction contract for Meta Muse Spark:
1. ExtractedFactCandidate: Model-proposed explicit operational fact (Date, Amount, Deadline, etc.).
2. RequirementCandidate: Model-proposed directive, obligation, or restriction.
3. StructuredExtractionResponse: Combined extraction payload.

Guarantees:
- Model output cannot control database primary keys, foreign keys, or verification statuses.
- Strict rejection of unsupported fact types or unexpected fields (extra="forbid").
- Original multilingual script (English, Malayalam) and combining marks are preserved intact.
- Reusable enums from app.models.enums are leveraged to ensure 100% database compatibility.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import FactType


class RequirementModality(StrEnum):
    """Deontic modalities for operational requirements."""

    MUST = "MUST"
    SHOULD = "SHOULD"
    MAY = "MAY"
    PROHIBITED = "PROHIBITED"


class RequirementType(StrEnum):
    """Common operational requirement categories."""

    OBLIGATION = "OBLIGATION"
    RESTRICTION = "RESTRICTION"
    PROHIBITION = "PROHIBITION"
    REPORTING = "REPORTING"
    DIRECTIVE = "DIRECTIVE"
    RESPONSIBILITY = "RESPONSIBILITY"
    APPROVAL = "APPROVAL"
    GENERAL = "GENERAL"


class ExtractedFactCandidate(BaseModel):
    """
    A model-proposed descriptive fact explicitly present in an operational document.

    Attributes:
        fact_type: One of the supported database FactType enums.
        raw_value: Original-language value as written in the source document.
        source_quote: Exact verbatim supporting quote copied from the document.
        normalized_value: Optional flexible JSON-compatible structured metadata.
        description: Optional contextual explanation or description of the fact.
    """

    model_config = ConfigDict(extra="forbid")

    fact_type: FactType = Field(
        ...,
        description="Categorical type of the fact (DATE, AMOUNT, CURRENCY, OBLIGATION, DEADLINE, APPROVAL_CONDITION, IDENTIFIER, RESPONSIBLE_DEPARTMENT).",
    )
    raw_value: str = Field(
        ...,
        description="Original-language value as stated in the document (e.g. '30 നവംബർ 2026', '₹5,00,000').",
    )
    source_quote: str = Field(
        ...,
        description="Exact verbatim sentence or clause copied from the document page confirming this fact.",
    )
    normalized_value: dict[str, Any] | None = Field(
        default=None,
        description="Flexible structured metadata (e.g. ISO date, numeric amount, currency code).",
    )
    description: str | None = Field(
        default=None,
        description="Optional contextual explanation of the fact.",
    )

    @field_validator("fact_type", mode="before")
    @classmethod
    def validate_fact_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.strip().upper()
            if v_clean in FactType._value2member_map_:
                return FactType(v_clean)
        return v

    @field_validator("raw_value")
    @classmethod
    def validate_raw_value(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_value cannot be empty or whitespace-only")
        return v

    @field_validator("source_quote")
    @classmethod
    def validate_source_quote(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_quote cannot be empty or whitespace-only")
        return v


class RequirementMetadata(BaseModel):
    """
    Structured operational metadata for an extracted requirement.
    All fields are optional; models must not invent missing actors, deadlines, or conditions.
    """

    model_config = ConfigDict(extra="forbid")

    responsible_actor: str | None = Field(
        default=None,
        description="Entity, role, or department responsible for executing the requirement.",
    )
    required_action: str | None = Field(
        default=None,
        description="The specific action that must, should, or may be performed.",
    )
    target_entity: str | None = Field(
        default=None,
        description="Entity, department, or system affected by or receiving the action.",
    )
    modality: RequirementModality | None = Field(
        default=None,
        description="Deontic modality (MUST, SHOULD, MAY, PROHIBITED).",
    )
    conditions: list[str] | None = Field(
        default=None,
        description="List of conditional triggers, prerequisites, or caveats.",
    )
    deadline: str | None = Field(
        default=None,
        description="Explicit deadline or timeframe (e.g. '2026-11-30', '30 days from notice').",
    )
    frequency: str | None = Field(
        default=None,
        description="Recurrence frequency (e.g. 'MONTHLY', 'QUARTERLY', 'ANNUAL', 'AS_NEEDED').",
    )
    scope: str | None = Field(
        default=None,
        description="Applicable operational or organizational scope.",
    )
    original_language: str | None = Field(
        default=None,
        description="Source text language code (e.g. 'en', 'ml').",
    )
    original_language_text: str | None = Field(
        default=None,
        description="Verbatim requirement text in the original language/script.",
    )

    @field_validator("modality", mode="before")
    @classmethod
    def validate_modality(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            v_clean = v.strip().upper()
            if v_clean in RequirementModality._value2member_map_:
                return RequirementModality(v_clean)
            raise ValueError(f"Invalid modality '{v}'. Supported: {', '.join(RequirementModality)}")
        return v


class RequirementCandidate(BaseModel):
    """
    A model-proposed operational requirement (obligation, directive, restriction, responsibility).

    Attributes:
        title: Concise summary title of the requirement.
        description: Comprehensive description of the directive or obligation.
        requirement_type: Category of the requirement (e.g. 'OBLIGATION', 'RESTRICTION', 'DIRECTIVE').
        source_quote: Exact verbatim supporting quote copied from the document.
        normalized_value: Optional structured metadata (actor, action, modality, deadline, etc.).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        ...,
        description="Concise summary title of the requirement.",
    )
    description: str = Field(
        ...,
        description="Detailed description of what is required, permitted, or prohibited.",
    )
    requirement_type: RequirementType = Field(
        default=RequirementType.OBLIGATION,
        description="Category of the requirement (e.g. OBLIGATION, DIRECTIVE, RESTRICTION, REPORTING).",
    )
    source_quote: str = Field(
        ...,
        description="Exact verbatim supporting quote copied from the document.",
    )
    normalized_value: RequirementMetadata | None = Field(
        default=None,
        description="Optional structured metadata (responsible actor, action, modality, deadline).",
    )

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title cannot be empty or whitespace-only")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("description cannot be empty or whitespace-only")
        return v

    @field_validator("requirement_type", mode="before")
    @classmethod
    def validate_requirement_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.strip().upper()
            if v_clean in RequirementType._value2member_map_:
                return RequirementType(v_clean)
            raise ValueError(
                f"Invalid requirement_type '{v}'. Supported: {', '.join(RequirementType)}"
            )
        return v

    @field_validator("source_quote")
    @classmethod
    def validate_source_quote(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_quote cannot be empty or whitespace-only")
        return v


class StructuredExtractionResponse(BaseModel):
    """
    Combined structured extraction output from Meta Muse Spark.
    Contains model-proposed facts and requirements. Both lists may legitimately be empty.
    """

    model_config = ConfigDict(extra="forbid")

    facts: list[ExtractedFactCandidate] = Field(
        default_factory=list,
        description="List of model-proposed operational facts.",
    )
    requirements: list[RequirementCandidate] = Field(
        default_factory=list,
        description="List of model-proposed operational requirements.",
    )
