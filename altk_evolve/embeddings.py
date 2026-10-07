"""Shared embedding loader for storage, guideline selection and consistency."""

from functools import lru_cache
from typing import Literal, Protocol, cast

import numpy as np
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")
    embedding_provider: Literal["sentence_transformers", "fastembed"] = "sentence_transformers"


class EmbeddingModel(Protocol):
    def encode(self, sentences: str | list[str], *, normalize_embeddings: bool = False) -> np.ndarray: ...
    def similarity(self, a, b): ...
    def get_sentence_embedding_dimension(self) -> int: ...


class FastEmbedModel:
    """Sentence embedding interface backed by CPU ONNX inference.

    Uses FastEmbed's cache convention (including FASTEMBED_CACHE_PATH). No
    multiprocessing is started; callers may share the cached model in a process.
    """

    def __init__(self, model_name: str, cache_dir: str | None):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self._dimension = int(next(item["dim"] for item in TextEmbedding.list_supported_models() if item["model"] == model_name))

    def encode(self, sentences: str | list[str], *, normalize_embeddings: bool = False) -> np.ndarray:
        single = isinstance(sentences, str)
        texts = [sentences] if single else sentences
        if not texts:
            return np.empty((0, self._dimension), dtype=np.float32)
        vectors = np.asarray(list(self._model.embed(texts, parallel=None)), dtype=np.float32)
        if normalize_embeddings:
            vectors = _normalize(vectors)
        return vectors[0] if single else vectors

    def similarity(self, a, b) -> np.ndarray:
        return np.asarray(np.clip(_normalize(np.atleast_2d(a)) @ _normalize(np.atleast_2d(b)).T, -1.0, 1.0))

    def get_sentence_embedding_dimension(self) -> int:
        return self._dimension


def _normalize(vectors) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.asarray(vectors / np.maximum(norms, np.finfo(np.float32).tiny))


def get_embedding_model(model_name: str, *, trust_remote_code: bool = False) -> EmbeddingModel:
    import os

    return _load_embedding_model(
        EmbeddingSettings().embedding_provider,
        model_name,
        trust_remote_code,
        os.environ.get("FASTEMBED_CACHE_PATH"),
    )


@lru_cache(maxsize=4)
def _load_embedding_model(provider: str, model_name: str, trust_remote_code: bool, cache_dir: str | None) -> EmbeddingModel:
    if provider == "fastembed":
        if trust_remote_code:
            raise ValueError("FastEmbed does not support trust_remote_code; use a supported ONNX model")
        return FastEmbedModel(model_name, cache_dir)
    from sentence_transformers import SentenceTransformer

    return cast(EmbeddingModel, SentenceTransformer(model_name, trust_remote_code=trust_remote_code))
