"""Append-only audit log writer for .evolve/audit.log."""

import datetime
import json
import pathlib


def append(project_root=".", **fields):
    """Append a JSON audit entry to .evolve/audit.log.

    Args:
        project_root: Root directory that contains .evolve/.
        **fields: Arbitrary key-value fields to include in the log entry.
    """
    path = pathlib.Path(project_root) / ".evolve" / "audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {**fields, "ts": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        append(project_root=d, action="test", actor="alice")
        log_path = __import__("pathlib").Path(d) / ".evolve" / "audit.log"
        line = log_path.read_text(encoding="utf-8").strip()
        entry = __import__("json").loads(line)
        assert entry["action"] == "test"
        assert entry["actor"] == "alice"
        assert "ts" in entry
    print("audit.py ok")
