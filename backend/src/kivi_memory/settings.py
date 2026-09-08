from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir
from pydantic import Field
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
    chat_model: str = "sarvam-105b"
    model_reasoning_effort: str | None = None
    model_connect_timeout_seconds: float = 10.0
    model_read_timeout_seconds: float = 60.0
    query_deadline_seconds: float = 90.0
    max_import_bytes: int = 10 * 1024 * 1024
    max_record_bytes: int = 256 * 1024
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_cache_dir: Path | None = None
    frontend_dist_dir: Path | None = None
    trusted_origins: str = "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173"

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
