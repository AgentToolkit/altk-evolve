"""Write stage output files atomically, so a failed run never leaves a partial file behind."""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any


def _create_temp(path: Path) -> tuple[int, Path]:
    """A new temp file beside path, created 0666 so the OS applies the umask as open(path, "w") would.

    Reading the umask would mean setting it, and that is process-wide: any file another
    thread created in between would get the wrong mode. Letting the kernel apply it avoids that.
    """
    for _ in range(100):
        temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), temp
        except FileExistsError:
            continue
    raise FileExistsError(f"could not create a temp file beside {path}")


def write_json_atomic(path: Path, document: Any) -> None:
    """Serialize document, write it to a temp file beside path, then rename it over path.

    Serialization happens before any file is touched, and the rename is atomic on
    one filesystem, so readers see either the previous file or the complete new
    one. The result has the mode a plain write would leave: an existing file keeps
    its own, a new one gets 0666 less the umask. On any error the temp file is
    removed and the error propagates.
    """
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = _create_temp(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
        except FileNotFoundError:
            pass
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
