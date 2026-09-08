from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TranscriptRecord(BaseModel):
    schema_version: Literal[1]
    id: str = Field(min_length=1)
    raw_asr: str
    formatted_text: str
    occurred_at: datetime | None = None
    timezone: str | None = None
    app: str | None = None
    language_hints: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("id must contain a non-whitespace character")
        return value


class NamespaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ValidateImportRequest(BaseModel):
    jsonl: str


class ImportRequest(BaseModel):
    jsonl: str
    replace_conflicts: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["answer", "draft"] = "answer"


class CorrectionRequest(BaseModel):
    value: str = Field(min_length=1, max_length=5000)
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100)


class SuppressionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100)


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict[str, Any] | None = None
