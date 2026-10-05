"""Write stage output files atomically, so a failed run never leaves a partial file behind."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, document: Any) -> None:
    """Serialize document, write it to a temp file beside path, then rename it over path.

    Serialization happens before any file is touched, and the rename is atomic on
    one filesystem, so readers see either the previous file or the complete new
    one. On any error the temp file is removed and the error propagates.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)  # mkstemp creates 0600; these files are read by other tools
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
