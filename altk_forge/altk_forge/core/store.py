"""JSON files on disk. Every object Forge keeps is a JSON file under the data folder; writes go through a temporary file,
so a crash never leaves half a file, and secrets are written with mode 0600."""

import json
import os
import threading

# One lock for the files every part of Forge writes (integrations, filters, pipeline definitions, settings).
LOCK = threading.RLock()


def read_json(path, default=None):
    """The JSON value in `path`, or `default` when there is no such file."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write_json(path, value, private=False):
    """Write `value` to `path` atomically (through a temporary file); `private` makes it readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    if private:
        os.chmod(tmp, 0o600)
    tmp.replace(path)
