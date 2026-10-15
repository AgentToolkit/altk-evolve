"""Exercise an installed wheel outside the source tree: core or convenience."""

import argparse
from importlib.metadata import distribution, distributions, version
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile


def main(mode: str) -> None:
    import altk_evolve
    from altk_evolve.config.evolve import EvolveConfig
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.embeddings import EmbeddingSettings, get_embedding_model
    from altk_evolve.sync.phoenix_sync import PhoenixSync
    from altk_evolve.processing.builtin import GuidelineConfig, GuidelineProcessor

    assert GuidelineProcessor.from_config(GuidelineConfig()) is not None

    assert PhoenixSync is not None
    assert altk_evolve.__version__ == version("altk-evolve-core")
    assert (Path(altk_evolve.__file__).parent / "frontend/ui/dist/index.html").is_file()
    installed = {d.metadata["Name"].lower().replace("_", "-") for d in distributions()}
    assert not {"arize-phoenix", "torch", "sentence-transformers", "readi-privacy", "udtools", "udapi"} & installed
    assert EmbeddingSettings().embedding_provider == "fastembed"
    with tempfile.TemporaryDirectory() as data:
        client = EvolveClient(EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=data)))
        client.create_namespace("packaging-smoke")
        assert client.get_namespace_details("packaging-smoke").id == "packaging-smoke"
    if mode == "core":
        assert not {"pandas", "scipy", "fastapi", "fastmcp", "uvicorn", "typer", "opentelemetry-sdk"} & installed
        assert "altk-evolve" not in installed
        assert importlib.util.find_spec("fastembed") is None
        try:
            get_embedding_model("sentence-transformers/all-MiniLM-L6-v2")
        except ImportError as exc:
            assert "altk-evolve-core[fastembed]" in str(exc)
        else:
            raise AssertionError("Core unexpectedly supplied an embedding runtime")
    else:
        from altk_evolve.frontend.mcp.mcp_server import mcp

        assert mcp is not None
        subprocess.run([str(Path(sys.executable).parent / "evolve"), "--help"], check=True, stdout=subprocess.DEVNULL)
        from fastembed import TextEmbedding
        from phoenix.otel import register

        assert callable(register)
        assert TextEmbedding.list_supported_models()
        model = get_embedding_model("sentence-transformers/all-MiniLM-L6-v2")
        vectors = model.encode(["Remember useful context.", "Remember useful context."], normalize_embeddings=True)
        assert vectors.shape == (2, 384)
        assert abs(float(model.similarity(vectors, vectors)[0, 1]) - 1.0) < 1e-5
        assert version("altk-evolve") == version("altk-evolve-core")
        assert all(".dist-info/" in str(path) for path in distribution("altk-evolve").files or [])
    print(f"{mode}: imports, filesystem storage, and dependency boundaries passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("core", "convenience"))
    main(parser.parse_args().mode)
