"""Local multilingual E5 vectors with an explicit offline-only loading policy."""
from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np

from .settings import Settings


@lru_cache(maxsize=1)
def _encoder(model_name: str, cache_dir: str, download: bool):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, cache_folder=cache_dir, local_files_only=not download)


def load_encoder(settings: Settings, *, download: bool = False):
    return _encoder(settings.embedding_model, str(settings.embeddings_dir), download)


def model_is_cached(settings: Settings) -> bool:
    cache_name = f"models--{settings.embedding_model.replace('/', '--')}"
    return (settings.embeddings_dir / cache_name).exists()


def encode_passage(settings: Settings, value: str) -> tuple[bytes, int] | None:
    if not model_is_cached(settings):
        return None
    try:
        vector = load_encoder(settings).encode([f"passage: {value}"], normalize_embeddings=True)[0]
    except Exception:  # noqa: BLE001 - missing/corrupt local model must leave retrieval available.
        return None
    normalized = np.asarray(vector, dtype=np.float32)
    return normalized.tobytes(), int(normalized.size)


def encode_query(settings: Settings, value: str) -> np.ndarray | None:
    if not model_is_cached(settings):
        return None
    try:
        return np.asarray(load_encoder(settings).encode([f"query: {value}"], normalize_embeddings=True)[0], dtype=np.float32)
    except Exception:  # noqa: BLE001 - missing/corrupt local model must leave retrieval available.
        return None


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def vector_from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
