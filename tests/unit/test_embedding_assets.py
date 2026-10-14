import json

import pytest

from altk_evolve.embedding_assets import (
    ARTIFACT_FILES,
    CODERANK_MODEL,
    CODERANK_REVISION,
    EXPORT_VERSION,
    file_sha256,
    validate_coderank_artifact,
)


@pytest.mark.unit
def test_artifact_manifest_requires_pinned_source_and_intact_files(tmp_path):
    for name in ARTIFACT_FILES:
        (tmp_path / name).write_bytes(b"test artifact")
    manifest = {
        "source": CODERANK_MODEL,
        "revision": CODERANK_REVISION,
        "export_version": EXPORT_VERSION,
        "precision": "fp32",
        "sha256": {name: file_sha256(tmp_path / name) for name in ARTIFACT_FILES},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    validate_coderank_artifact(tmp_path)
    manifest["revision"] = "untrusted-revision"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="export_embeddings"):
        validate_coderank_artifact(tmp_path)
    manifest["revision"] = CODERANK_REVISION
    path.write_text(json.dumps(manifest))
    (tmp_path / "model.onnx").write_bytes(b"modified")
    with pytest.raises(ValueError, match="export_embeddings"):
        validate_coderank_artifact(tmp_path)


@pytest.mark.unit
def test_missing_export_has_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="fastembed-export"):
        validate_coderank_artifact(tmp_path)
