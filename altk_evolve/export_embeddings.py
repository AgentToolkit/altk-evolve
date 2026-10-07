"""Build CodeRankEmbed FP32 ONNX from pinned official weights, never at inference time."""

import argparse
import importlib.metadata
import json
import os
import shutil
import tempfile
from pathlib import Path

from altk_evolve.embedding_assets import (
    ARTIFACT_FILES,
    CODERANK_MODEL,
    CODERANK_REVISION,
    EXPORT_VERSION,
    coderank_directory,
    file_sha256,
    validate_coderank_artifact,
)

# Include different lengths, padding, code, prose, negation and a long input.
VALIDATION_TEXTS = [
    "def add(a, b): return a + b",
    "def add(a, b): return a - b",
    "SELECT * FROM users WHERE active = true",
    "SELECT * FROM users WHERE active = false",
    "Delete expired memories unless they are under legal hold.",
    "Keep memories that are subject to a legal hold.",
    "",
    " ".join(["def calculate_total(items): return sum(item.price for item in items)"] * 60),
]


def export_coderank(cache_dir: str | None = None) -> Path:
    """Publish a validated cache directory atomically; leave existing valid exports alone."""
    target = coderank_directory(cache_dir)
    if target.exists():
        validate_coderank_artifact(target)
        return target

    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from huggingface_hub import snapshot_download
    from sentence_transformers import SentenceTransformer

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".coderank-export-", dir=target.parent) as scratch:
        work = Path(scratch)
        source = snapshot_download(
            CODERANK_MODEL,
            revision=CODERANK_REVISION,
            cache_dir=work / "source",
            allow_patterns=["*.json", "*.py", "*.safetensors", "*.txt"],
        )
        model = SentenceTransformer(source, device="cpu", trust_remote_code=True).eval()
        model.float()
        output = work / "artifact"
        output.mkdir()
        model.tokenizer.save_pretrained(output)
        if not (output / "special_tokens_map.json").exists():
            shutil.copyfile(Path(source) / "special_tokens_map.json", output / "special_tokens_map.json")
        shutil.copyfile(Path(source) / "config.json", output / "config.json")

        class Encoder(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder = model

            def forward(self, input_ids, attention_mask):
                return self.encoder({"input_ids": input_ids, "attention_mask": attention_mask})["sentence_embedding"]

        example = model.tokenizer(VALIDATION_TEXTS[:2], padding=True, return_tensors="pt")
        torch.onnx.export(
            Encoder().eval(),
            (example["input_ids"], example["attention_mask"]),
            output / "model.onnx",
            input_names=["input_ids", "attention_mask"],
            output_names=["sentence_embedding"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "sentence_embedding": {0: "batch"},
            },
            opset_version=17,
            dynamo=False,
            external_data=False,
        )
        onnx.checker.check_model(str(output / "model.onnx"))
        session = ort.InferenceSession(str(output / "model.onnx"), providers=["CPUExecutionProvider"])
        reference = model.encode(VALIDATION_TEXTS, batch_size=2, normalize_embeddings=True)
        results = []
        for start in range(0, len(VALIDATION_TEXTS), 2):
            tokens = model.tokenizer(
                VALIDATION_TEXTS[start : start + 2], padding=True, truncation=True, max_length=8192, return_tensors="np"
            )
            vectors = session.run(None, {name: tokens[name] for name in ("input_ids", "attention_mask")})[0]
            results.extend(vectors)
        actual = np.asarray(results)
        actual /= np.maximum(np.linalg.norm(actual, axis=1, keepdims=True), 1e-12)
        if actual.shape != reference.shape or not np.isfinite(actual).all():
            raise ValueError("ONNX validation returned invalid embeddings")
        max_delta = float(np.abs(reference @ reference.T - actual @ actual.T).max())
        min_cosine = float(np.sum(reference * actual, axis=1).min())
        if max_delta > 1e-4 or min_cosine < 0.9999:
            raise ValueError(f"ONNX validation failed: similarity delta={max_delta}, cosine={min_cosine}")
        manifest = {
            "source": CODERANK_MODEL,
            "revision": CODERANK_REVISION,
            "export_version": EXPORT_VERSION,
            "precision": "fp32",
            "opset": 17,
            "pooling": "cls",
            "dimension": 768,
            "max_sequence_length": 8192,
            "sha256": {name: file_sha256(output / name) for name in ARTIFACT_FILES},
            "validation": {"max_pairwise_delta": max_delta, "min_aligned_cosine": min_cosine},
            "versions": {
                name: importlib.metadata.version(name) for name in ("torch", "onnx", "onnxruntime", "sentence-transformers", "transformers")
            },
        }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        validate_coderank_artifact(output)
        try:
            os.rename(output, target)
        except OSError:
            # Another builder may have finished the same export first.
            validate_coderank_artifact(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default=None, help="Defaults to FASTEMBED_CACHE_PATH")
    args = parser.parse_args()
    print(export_coderank(args.cache_dir))


if __name__ == "__main__":
    main()
