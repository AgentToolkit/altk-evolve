"""Pinned source and manifest for Evolve's locally generated CodeRankEmbed ONNX."""

import hashlib
import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CODERANK_MODEL = "nomic-ai/CodeRankEmbed"
CODERANK_REVISION = "3c4b60807d71f79b43f3c4363786d9493691f8b1"  # pragma: allowlist secret (public HF revision)
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EXPORT_VERSION = 1
ARTIFACT_FILES = ("model.onnx", "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")


class FastEmbedCacheSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    fastembed_cache_path: str | None = Field(default=None, validation_alias="FASTEMBED_CACHE_PATH")


def coderank_directory(cache_dir: str | None = None) -> Path:
    root = Path(cache_dir or FastEmbedCacheSettings().fastembed_cache_path or Path.home() / ".cache" / "fastembed")
    return root / f"evolve-coderankembed-fp32-{CODERANK_REVISION[:12]}-v{EXPORT_VERSION}"


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_coderank_artifact(directory: Path) -> None:
    """Reject incomplete, modified or incompatible exports before loading ONNX."""
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        if (
            manifest["source"] != CODERANK_MODEL
            or manifest["revision"] != CODERANK_REVISION
            or manifest["export_version"] != EXPORT_VERSION
            or manifest["precision"] != "fp32"
        ):
            raise ValueError("incompatible manifest")
        for name in ARTIFACT_FILES:
            if file_sha256(directory / name) != manifest["sha256"][name]:
                raise ValueError(f"checksum mismatch: {name}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"CodeRankEmbed ONNX assets are missing or invalid at {directory}. "
            "Prepare them with: python -m altk_evolve.export_embeddings "
            "(install altk-evolve[fastembed-export] on the build machine)."
        ) from exc
