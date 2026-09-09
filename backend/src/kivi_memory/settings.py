from __future__ import annotations

from pathlib import Path
from typing import Any

from platformdirs import user_data_dir
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Runtime settings. Secrets are deliberately server-only."""

    # Resolve beside the backend project, not against whichever shell directory
    # launches the CLI. This matches the documented backend/.env location.
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_data_dir: Path = Field(default_factory=lambda: Path(user_data_dir("KiviMemoryWorkbench")))
    database_url: str | None = None
    chat_base_url: str = "https://api.sarvam.ai/v1"
    sarvam_api_key: str | None = None
    api_key: str | None = None

    @field_validator("app_data_dir", mode="before")
    @classmethod
    def _parse_app_data_dir(cls, v: Any) -> Path:
        if not v or str(v).strip() in ("", "."):
            return Path(user_data_dir("KiviMemoryWorkbench")).resolve()
        return Path(v).resolve()

    @field_validator("embedding_cache_dir", mode="before")
    @classmethod
    def _parse_embedding_cache_dir(cls, v: Any) -> Path | None:
        if not v or not str(v).strip():
            return None
        return Path(v).resolve()

    @field_validator("sarvam_api_key", "api_key", "database_url", "model_reasoning_effort", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v
    chat_model: str = "sarvam-105b"
    provider_input_inr_per_million: float = Field(default=29.28, ge=0)
    provider_output_inr_per_million: float = Field(default=73.2, ge=0)
    provider_pricing_as_of: str = "2026-09-09"
    model_reasoning_effort: str | None = None
    model_connect_timeout_seconds: float = 10.0
    model_read_timeout_seconds: float = 60.0
    query_deadline_seconds: float = 90.0
    model_max_attempts: int = Field(default=2, ge=1, le=5)
    max_import_bytes: int = 10 * 1024 * 1024
    max_record_bytes: int = 256 * 1024
    max_evidence_chars_per_source: int = Field(default=4000, ge=500, le=20000)
    max_evidence_chars_total: int = Field(default=24000, ge=1000, le=100000)
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_min_similarity: float = Field(default=0.72, ge=-1.0, le=1.0)
    embedding_cache_dir: Path | None = None
    # Pillar 4: Semantic intelligence settings
    enable_semantic_extraction: bool = True
    multi_hop_max_depth: int = Field(default=2, ge=1, le=3)
    multi_hop_bridge_limit: int = Field(default=4, ge=1, le=10)
    decay_half_life_days: float = Field(default=1.0, ge=0.1, le=30.0)
    contradiction_time_window_hours: float = Field(default=1.0, ge=0.0, le=24.0)
    frontend_dist_dir: Path | None = None
    trusted_origins: str = (
        "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,"
        "https://hey-kivi-sarvam-dnaq.vercel.app"
    )

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(self.app_data_dir / 'memory.sqlite3').as_posix()}"

    @property
    def origins(self) -> set[str]:
        return {origin.strip().rstrip("/") for origin in self.trusted_origins.split(",") if origin.strip()}

    @property
    def embeddings_dir(self) -> Path:
        path = self.embedding_cache_dir or (self.app_data_dir / "embeddings")
        path.mkdir(parents=True, exist_ok=True)
        return path
