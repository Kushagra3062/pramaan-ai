"""Strict file schemas for the offline extraction benchmark and predictions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.extraction import StructuredExtractionResponse


class EvidenceProvenance(BaseModel):
    """Trusted benchmark annotation or candidate-declared evidence location."""

    model_config = ConfigDict(extra="forbid")

    candidate_index: int = Field(ge=0)
    page_number: int = Field(ge=1)
    chunk_id: str = Field(min_length=1)


class BenchmarkCase(BaseModel):
    """One human-authored synthetic source and its expected structured result."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    language: Literal["en", "ml", "mixed"]
    canonical_text: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    chunk_id: str = Field(min_length=1)
    categories: list[str] = Field(min_length=1)
    expected: StructuredExtractionResponse
    fact_provenance: list[EvidenceProvenance] = Field(default_factory=list)
    requirement_provenance: list[EvidenceProvenance] = Field(default_factory=list)

    @field_validator("fact_provenance", "requirement_provenance")
    @classmethod
    def unique_candidate_indexes(cls, values: list[EvidenceProvenance]) -> list[EvidenceProvenance]:
        indexes = [value.candidate_index for value in values]
        if len(indexes) != len(set(indexes)):
            raise ValueError("candidate provenance indexes must be unique")
        return values


class BenchmarkDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(min_length=1)
    cases: list[BenchmarkCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def unique_case_ids(cls, cases: list[BenchmarkCase]) -> list[BenchmarkCase]:
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique")
        return cases


class PredictionCase(BaseModel):
    """One candidate response. The nested response is the production extraction schema."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    response: StructuredExtractionResponse
    fact_provenance: list[EvidenceProvenance] = Field(default_factory=list)
    requirement_provenance: list[EvidenceProvenance] = Field(default_factory=list)


class PredictionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    predictions: list[PredictionCase] = Field(default_factory=list)

    @field_validator("predictions")
    @classmethod
    def unique_case_ids(cls, cases: list[PredictionCase]) -> list[PredictionCase]:
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("prediction case_id values must be unique")
        return cases
