"""Shared embedding loader for storage, guideline selection and consistency."""

from functools import lru_cache
from threading import Lock
from typing import Literal, Protocol, cast

import numpy as np
from pydantic_settings import BaseSettings, SettingsConfigDict

from altk_evolve.embedding_assets import CODERANK_MODEL, CODERANK_REVISION, coderank_directory, validate_coderank_artifact

_registration_lock = Lock()


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

        options = {}
        if model_name == CODERANK_MODEL:
            from fastembed.common.model_description import ModelSource, PoolingType

            directory = coderank_directory(cache_dir)
            validate_coderank_artifact(directory)
            with _registration_lock:
                if not any(item["model"] == model_name for item in TextEmbedding.list_supported_models()):
                    TextEmbedding.add_custom_model(
                        model=model_name,
                        pooling=PoolingType.DISABLED,  # CLS pooling is part of Evolve's exported graph.
                        normalization=True,
                        sources=ModelSource(hf=CODERANK_MODEL),
                        dim=768,
                        model_file="model.onnx",
                        description="Evolve-owned FP32 export of pinned official CodeRankEmbed weights",
                        license="MIT",
                    )
            options["specific_model_path"] = str(directory)
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir, **options)
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
        if trust_remote_code and model_name != CODERANK_MODEL:
            raise ValueError("FastEmbed does not support trust_remote_code; use a supported ONNX model")
        return FastEmbedModel(model_name, cache_dir)
    from sentence_transformers import SentenceTransformer

    if model_name == CODERANK_MODEL:
        # This built-in model uses reviewed, pinned upstream custom code.
        return cast(
            EmbeddingModel,
            SentenceTransformer(model_name, revision=CODERANK_REVISION, trust_remote_code=True),
        )
    return cast(EmbeddingModel, SentenceTransformer(model_name, trust_remote_code=trust_remote_code))
