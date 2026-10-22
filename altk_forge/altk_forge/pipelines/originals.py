"""The original traces a pipeline imported, kept next to the trajectories made of them: a snapshot of exactly what the
source gave, so a trajectory can always be checked against (or rebuilt from) the trace it came from, even after the
source has changed or deleted it.

  .data/pipelines/<id>/traces/<trace_id>/trace.langfuse.json   a Langfuse trace: the API response as it came
                                                                (GET /api/public/traces/<id>, what cli.build reads)
  .data/pipelines/<id>/traces/<trace_id>/<file name>           a file from a folder: a byte-for-byte copy
  .data/pipelines/<id>/traces/<trace_id>/source.json           where that file came from: path, uri, modified_at, size

A snapshot is written when the trace is downloaded, before it is converted, so a trace that fails to convert keeps
one too. Downloading the trace again (a retry, or a trace that changed) replaces it."""

import json
import shutil

from ..core.errors import InputError
from ..core.paths import pipe_dir
from ..core.store import write_json
from .trajectories import TRACE_ID

LANGFUSE_FILE = "trace.langfuse.json"
SOURCE_FILE = "source.json"
# What the folder source puts around a file's content; kept in source.json, without the content itself.
FILE_FIELDS = ("name", "path", "uri", "modified_at", "size")


def originals_dir(pipeline_id, trace_id=None):
    root = pipe_dir(pipeline_id) / "traces"
    if trace_id is None:
        return root
    if not TRACE_ID.match(trace_id or "") or trace_id in (".", ".."):
        raise InputError("Invalid id")
    return root / trace_id


def save_original(pipeline_id, trace_id, raw, client):
    """Keep the trace as the source gave it. `raw` is what client.trace() returned; a source that can give the
    original bytes (a folder) gives them through client.original()."""
    d = originals_dir(pipeline_id, trace_id)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    if hasattr(client, "original"):
        data, name = client.original(trace_id)
        # A file named like the description beside it keeps its name with a prefix, so neither overwrites the other.
        (d / (name if name != SOURCE_FILE else f"file.{name}")).write_bytes(data)
        write_json(d / SOURCE_FILE, {k: raw.get(k) for k in FILE_FIELDS})
    else:
        (d / LANGFUSE_FILE).write_text(json.dumps(raw, ensure_ascii=False, indent=1) + "\n")


def original(pipeline_id, trace_id):
    """(the snapshot of a trace as bytes, its file name), or None when there is none. The description of a file
    (source.json) is not the trace: the trace is the other file."""
    d = originals_dir(pipeline_id, trace_id)
    files = sorted(f for f in d.iterdir() if f.name != SOURCE_FILE) if d.exists() else []
    return (files[0].read_bytes(), files[0].name) if files else None


def delete_originals(pipeline_id, trace_ids=None):
    """Delete the snapshots of `trace_ids` (every one when None); returns how many there were."""
    root = originals_dir(pipeline_id)
    if not root.exists():
        return 0
    dirs = [d for d in root.iterdir() if d.is_dir()] if trace_ids is None else [originals_dir(pipeline_id, t) for t in trace_ids]
    dirs = [d for d in dirs if d.exists()]
    for d in dirs:
        shutil.rmtree(d)
    return len(dirs)
