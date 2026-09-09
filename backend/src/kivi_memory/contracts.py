from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TranscriptRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1]
    id: str = Field(min_length=1, max_length=256)
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

    @field_validator("occurred_at")
    @classmethod
    def timezone_aware_occurrence(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        return value

    @field_validator("language_hints")
    @classmethod
    def valid_language_hints(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("language_hints cannot contain blank values")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("language_hints must be unique")
        return cleaned

    @model_validator(mode="after")
    def has_transcript_text(self) -> TranscriptRecord:
        if not self.raw_asr.strip() and not self.formatted_text.strip():
            raise ValueError("raw_asr or formatted_text must contain text")
        return self


class NamespaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must contain a non-whitespace character")
        return value.strip()


class ValidateImportRequest(BaseModel):
    jsonl: str


class ImportRequest(BaseModel):
    jsonl: str
    replace_conflicts: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: Literal["answer", "draft"] = "answer"

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain a non-whitespace character")
        return value.strip()


class CorrectionRequest(BaseModel):
    value: str = Field(min_length=1, max_length=5000)
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100)

    @field_validator("value")
    @classmethod
    def nonblank_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must contain a non-whitespace character")
        return value.strip()

    @field_validator("operation_id")
    @classmethod
    def nonblank_operation_id(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 8:
            raise ValueError("operation_id must contain at least 8 non-whitespace characters")
        return clean


class SuppressionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    operation_id: str = Field(min_length=8, max_length=100)

    @field_validator("operation_id")
    @classmethod
    def nonblank_operation_id(cls, value: str) -> str:
        clean = value.strip()
        if len(clean) < 8:
            raise ValueError("operation_id must contain at least 8 non-whitespace characters")
        return clean


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict[str, Any] | None = None
