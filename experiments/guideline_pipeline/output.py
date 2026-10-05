"""Write stage output files atomically, so a failed run never leaves a partial file behind."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def _plain_write_mode(path: Path) -> int:
    """The mode open(path, "w") would leave: an existing file keeps its own, a new one gets 0666 less the umask."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


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
        os.chmod(temp_name, _plain_write_mode(path))  # mkstemp creates 0600
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
