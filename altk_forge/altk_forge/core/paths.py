"""Where Forge finds its files: the package, the folder of .env and .data/, and each pipeline's folder."""

import os
import re
from pathlib import Path

from .errors import InputError

PACKAGE = Path(__file__).resolve().parent.parent
# Where .env and .data/ live: the project folder (next to pyproject.toml) when Forge runs from its checkout, the
# current folder when it is installed as a package.
ROOT = PACKAGE.parent if (PACKAGE.parent / "pyproject.toml").exists() else Path.cwd()
# Everything Forge stores; FORGE_DATA moves it (a deployment, a test run).
DATA = Path(os.environ.get("FORGE_DATA") or ROOT / ".data").resolve()


def load_env(path=ROOT / ".env"):
    """Read KEY=VALUE pairs from .env into os.environ; variables already set win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# Each pipeline keeps what it produced in a folder of its own, named by its id.
PIPES = DATA / "pipelines"
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def pipe_dir(pipeline_id):
    """The folder of a pipeline; refuses ids that could escape the data folder."""
    if not ID.match(pipeline_id or ""):
        raise InputError(f"Invalid pipeline id {pipeline_id!r}")
    return PIPES / pipeline_id
