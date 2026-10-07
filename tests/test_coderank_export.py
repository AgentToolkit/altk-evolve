"""Opt-in real export test; downloads official weights and validates the resulting graph."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.e2e
@pytest.mark.skipif(os.environ.get("EVOLVE_TEST_EMBEDDING_EXPORT") != "1", reason="Requires model download and ONNX export")
def test_official_coderank_export_and_torch_free_runtime(tmp_path):
    from altk_evolve.export_embeddings import export_coderank

    path = export_coderank(str(tmp_path))
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["validation"]["fastembed_max_pairwise_delta"] < 1e-4
    assert manifest["validation"]["fastembed_min_aligned_cosine"] >= 0.9999
    config = json.loads((path / "tokenizer_config.json").read_text())
    assert config["max_length"] == config["model_max_length"] == 8192
    env = {**os.environ, "EVOLVE_EMBEDDING_PROVIDER": "fastembed", "FASTEMBED_CACHE_PATH": str(tmp_path), "HF_HUB_OFFLINE": "1"}
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from altk_evolve.embeddings import get_embedding_model; "
            "m=get_embedding_model('nomic-ai/CodeRankEmbed'); "
            "assert m.encode(['def f(): return 1', 'text']).shape == (2,768); "
            "assert 'torch' not in sys.modules; assert 'sentence_transformers' not in sys.modules",
        ],
        env=env,
        check=True,
    )
