"""A folder as a trace source: every *.json file in it (optionally its subfolders) is one trace, except files
matching the exclude patterns. Folders take no filters."""

import fnmatch
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .http import SourceError

MAX_FILE_BYTES = 50 << 20


class FileTraceError(ValueError):
    """A file that cannot be read as a trace (not JSON, too large, gone): that trace fails, the others go on."""


class Filesystem:
    """Every *.json file under a folder is one trace. Its id comes from the path relative to the folder, so it stays the
    same across polls; its time is the file's modification time. A trace is the file in an envelope:
    {id, name, path (relative), uri, modified_at, size, content (the parsed file)}."""

    def __init__(self, path, recursive=False, exclude=None):
        self.root = Path(os.path.expanduser(path or "")).resolve()
        self.recursive = bool(recursive)
        self.exclude = exclude_patterns(exclude)
        self._paths = None  # trace id -> file

    def check(self):
        n = sum(1 for _ in self._files())
        return (
            f"{n} JSON file{'s' * (n != 1)} in {self.root}"
            + (" and its subfolders" if self.recursive else "")
            + (f", not counting {', '.join(self.exclude)}" if self.exclude else "")
        )

    def _files(self):
        if not self.root.is_dir():
            raise SourceError(f"No folder {self.root}")
        if self.recursive:
            found = (Path(d) / n for d, dirs, names in _sorted_walk(self.root) for n in names)
        else:
            found = (f for f in sorted(self.root.iterdir()) if f.is_file())
        for f in found:
            if f.name.lower().endswith(".json") and not self.excluded(f.relative_to(self.root).as_posix()):
                yield f

    def excluded(self, rel):
        """A pattern without / matches the file name in any folder; with /, the path relative to the folder."""
        name = rel.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatchcase(rel if "/" in p else name, p) for p in self.exclude)

    @staticmethod
    def trace_id(rel):
        """A readable, stable id for a relative path, within what trajectory ids allow ([A-Za-z0-9_:.-], 128)."""
        readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", rel.removesuffix(".json").removesuffix(".JSON")).strip("_.")[:100]
        return f"{readable or 'file'}-{hashlib.sha1(rel.encode()).hexdigest()[:8]}"

    def list_traces(self, since=None, limit=None):
        """Files modified at or after `since` (UTC ISO), oldest first, as trace summaries."""
        out, self._paths = [], {}
        for f in self._files():
            try:
                st = f.stat()
            except OSError:
                continue  # removed while listing
            rel = f.relative_to(self.root).as_posix()
            tid = self.trace_id(rel)
            self._paths[tid] = f
            ts = _iso(st.st_mtime)
            if since and ts < since:
                continue
            out.append({"id": tid, "name": rel, "timestamp": ts, "updated_at": ts})
        out.sort(key=lambda t: (t["timestamp"], t["name"]))
        return out[:limit] if limit else out

    def trace(self, trace_id):
        if self._paths is None or trace_id not in self._paths:
            self.list_traces()
        f = self._paths.get(trace_id)
        if f is None:
            raise FileTraceError(f"No JSON file for trace {trace_id} in {self.root} any more")
        try:
            st = f.stat()
            if st.st_size > MAX_FILE_BYTES:
                raise FileTraceError(f"{f.name} is {st.st_size >> 20} MB; files over {MAX_FILE_BYTES >> 20} MB are not read")
            content = json.loads(f.read_bytes())
        except OSError as e:
            raise FileTraceError(f"Cannot read {f}: {e.strerror or e}") from None
        except ValueError as e:
            raise FileTraceError(f"{f.relative_to(self.root)} is not JSON: {e}") from None
        rel = f.relative_to(self.root).as_posix()
        return {
            "id": trace_id,
            "name": rel,
            "path": rel,
            "uri": f.as_uri(),
            "modified_at": _iso(st.st_mtime),
            "size": st.st_size,
            "content": content,
        }


def exclude_patterns(value):
    """Exclude patterns as a list: comma- or line-separated glob patterns (metadata.json, results*.json, sub/*.json)."""
    items = value if isinstance(value, list) else re.split(r"[,\n]", value or "")
    return [p.strip() for p in items if p and p.strip()]


def _sorted_walk(root):
    for d, dirs, names in os.walk(root):  # symlinked folders are not followed
        dirs.sort()
        yield d, dirs, sorted(names)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
