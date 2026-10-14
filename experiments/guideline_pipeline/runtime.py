"""Process setup that must run before an EvolveClient is constructed."""

from __future__ import annotations

import os
import sys


def stabilize_runtime() -> None:
    """Keep the embedding model on CPU with single-threaded BLAS/tokenizers on macOS.

    TEMPORARY: remove once Evolve lets callers choose the SentenceTransformer device.

    Evolve constructs ``SentenceTransformer(...)`` without a device, so on macOS it
    selects MPS. MPS combined with milvus-lite's forking gRPC client intermittently
    segfaults the interpreter, including mid-write. Hiding MPS from torch makes
    SentenceTransformer fall back to CPU, and pinning thread counts avoids the
    fork-heavy thread pools. The model is instantiated when the client is (or later,
    on first use), so calling this first is enough. Best-effort and idempotent.
    """
    if sys.platform != "darwin":
        return
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(1)
    mps = getattr(torch.backends, "mps", None)
    if mps is not None:
        mps.is_available = lambda: False
        mps.is_built = lambda: False
