"""Request/response schemas for the evaluation console (FR-024/FR-025)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvaluationDatasetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    evaluator_version: str
    case_count: int
    case_ids: list[str]
    languages: list[str]


class EvaluationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["mocked", "live"] = "mocked"
    predictions: dict[str, Any] | None = None
    prompt_version: str | None = Field(default=None, max_length=100)
    push_langfuse_scores: bool = False


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: uuid.UUID
    run_status: str
    mode: str
    dataset_version: str
    evaluator_version: str
    report: dict[str, Any]


class EvaluationRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    dataset_version: str
    evaluator_version: str
    status: str
    metrics: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None


class PaginatedEvaluationRunsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[EvaluationRunSummary]
    total: int
    limit: int
    offset: int


class EvaluationCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: str
    scores: dict[str, Any] | None = None
    expected_output: dict[str, Any] | None = None
    actual_output: dict[str, Any] | None = None


class EvaluationRunDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    dataset_version: str
    evaluator_version: str
    status: str
    metrics: dict[str, Any] | None = None
    cases: list[EvaluationCaseResponse]
    created_at: datetime
    completed_at: datetime | None = None
