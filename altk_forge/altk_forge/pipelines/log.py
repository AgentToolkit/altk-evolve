"""A pipeline's worker log: .data/pipelines/<id>/worker.log, the last 2 MB."""

from ..core.clock import now
from ..core.paths import pipe_dir

LOG_MAX = 2 << 20


def log(pipeline_id, line):
    path = pipe_dir(pipeline_id) / "worker.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX:
            path.write_text(path.read_text()[-LOG_MAX // 2 :])
        with path.open("a") as fh:
            fh.write(f"{now()} {line}\n")
    except OSError:
        pass


def read_log(pipeline_id, lines=300):
    path = pipe_dir(pipeline_id) / "worker.log"
    return "\n".join(path.read_text().splitlines()[-lines:]) if path.exists() else ""
