"""Shared entity I/O utilities for the Evolve plugin.

Handles reading and writing entities as flat markdown files with YAML
frontmatter, organized in type-nested directories.
"""

import datetime
import getpass
import os
import re
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _get_log_dir():
    """Get user-scoped log directory with restrictive permissions."""
    try:
        uid = os.getuid()
    except AttributeError:
        uid = getpass.getuser()
    log_dir = os.path.join(tempfile.gettempdir(), f"evolve-{uid}")
    os.makedirs(log_dir, mode=0o700, exist_ok=True)
    return log_dir


_LOG_FILE = os.path.join(_get_log_dir(), "evolve-plugin.log")


def log(component, message):
    """Append a timestamped message to the shared log file.

    Args:
        component: Short label like "retrieve" or "save".
        message: The log line.
    """
    if not os.environ.get("EVOLVE_DEBUG"):
        return
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{component}] {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------


def get_evolve_dir():
    """Return the .evolve root directory.

    Uses ``EVOLVE_DIR`` env var if set, otherwise ``.evolve/`` in cwd.
    Does not create the directory.
    """
    env_dir = os.environ.get("EVOLVE_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(".evolve")


def find_entities_dir():
    """Locate the entities directory.

    Uses :func:`get_evolve_dir` to determine the base directory, then
    returns the ``entities/`` subdirectory Path if it exists, else ``None``.
    """
    c = get_evolve_dir() / "entities"
    return c if c.is_dir() else None


def find_recall_entity_dirs():
    """Locate all directories that should be searched during recall.

    Returns the existing recall roots. Two trees contribute to recall:
    ``entities/`` (private entities in ``entities/guideline/`` and
    subscribed entities in ``entities/subscribed/{repo}/guideline/``) and
    ``public/`` (entities published by the local project).
    """
    evolve_dir = get_evolve_dir()
    candidates = [evolve_dir / "entities", evolve_dir / "public"]
    return [path for path in candidates if path.is_dir()]


def get_default_entities_dir():
    """Return (and create) the default entities directory.

    Uses ``EVOLVE_DIR`` if set, falls back to ``.evolve/entities/``.
    """
    base = get_evolve_dir() / "entities"
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


# ---------------------------------------------------------------------------
# Slugify / filename helpers
# ---------------------------------------------------------------------------


def slugify(text, max_length=60):
    """Convert *text* to a filesystem-safe slug.

    >>> slugify("Use temp files for JSON transfer!")
    'use-temp-files-for-json-transfer'
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    # Truncate at max_length, but don't break in the middle of a word
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text or "entity"


def claude_project_slug(path):
    """Derive Claude's per-project directory name from an absolute path.

    Claude names a project's ``~/.claude/projects/<slug>/`` directory by
    replacing every non-alphanumeric character in the resolved absolute project
    path with ``-``.

    >>> claude_project_slug("/Users/x/evolve-smoke-test2")
    '-Users-x-evolve-smoke-test2'

    This is the single source of truth shared by doctor.py (transcript dir) and
    adapt_memory.py (native memory dir).
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).resolve()))


def claude_memory_dir(path, home=None):
    """Return the native Claude memory dir for the project rooted at *path*.

    ``~/.claude/projects/<slug>/memory/`` where ``<slug>`` is
    :func:`claude_project_slug` of *path*. *home* defaults to ``Path.home()``.
    """
    home = Path.home() if home is None else Path(home)
    return home / ".claude" / "projects" / claude_project_slug(path) / "memory"


def sanitize_type(text):
    """Sanitize an entity *type* into a filesystem-safe subdirectory name.

    Like :func:`slugify` but without truncation — a type is a short label,
    not free-form content, and truncating it could silently merge distinct
    types. Returns an empty string for input that contains no usable
    characters, leaving the fallback decision to the caller.
    """
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def unique_filename(directory, slug):
    """Return a Path that doesn't collide with existing files in *directory*.

    Tries ``slug.md``, then ``slug-2.md``, ``slug-3.md``, etc.
    """
    directory = Path(directory)
    candidate = directory / f"{slug}.md"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = directory / f"{slug}-{n}.md"
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# Markdown <-> dict conversion
# ---------------------------------------------------------------------------

_FRONTMATTER_KEYS = ("type", "trigger", "trajectory", "owner", "source", "native_path", "visibility", "published_at")


def entity_to_markdown(entity):
    """Serialize an entity dict to markdown with YAML frontmatter.

    Args:
        entity: dict with keys ``content``, and optionally ``type``,
                ``trigger``, ``rationale``.

    Returns:
        A string suitable for writing to a ``.md`` file.
    """
    lines = ["---"]
    for key in _FRONTMATTER_KEYS:
        val = entity.get(key)
        if val:
            lines.append(f"{key}: {val}")
    lines.append("---")
    lines.append("")

    content = entity.get("content", "")
    lines.append(content)

    rationale = entity.get("rationale")
    if rationale:
        lines.append("")
        lines.append("## Rationale")
        lines.append("")
        lines.append(rationale)

    lines.append("")
    return "\n".join(lines)


def markdown_to_entity(path):
    """Parse a markdown entity file back into a dict.

    Handles YAML frontmatter with simple ``key: value`` lines (no nested
    structures, no PyYAML dependency).

    Returns:
        dict with ``content``, ``type``, ``trigger``, ``rationale`` keys.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    entity = {}

    # Split frontmatter
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2]
            for line in frontmatter.splitlines():
                line = line.strip()
                if not line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key and value:
                    entity[key] = value
        else:
            body = text
    else:
        body = text

    # Split body into content and rationale
    body = body.strip()
    m = re.search(r"^## Rationale", body, re.MULTILINE)
    if m:
        content = body[: m.start()].strip()
        rationale = body[m.end() :].strip()
        if rationale:
            entity["rationale"] = rationale
    else:
        content = body

    if content:
        entity["content"] = content

    return entity


def _parse_frontmatter_lines(lines):
    """Parse simple YAML-style frontmatter lines into a dict."""
    entity = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key and value:
            entity[key] = value
    return entity


def _parse_frontmatter_only(path):
    """Parse only the frontmatter section from a markdown entity file."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as handle:
            if handle.readline().strip() != "---":
                return {}

            frontmatter_lines = []
            found_closing = False
            for line in handle:
                if line.strip() == "---":
                    found_closing = True
                    break
                frontmatter_lines.append(line)
    except (OSError, UnicodeDecodeError):
        return {}

    if not found_closing:
        return {}

    return _parse_frontmatter_lines(frontmatter_lines)


def _manifest_path(path):
    """Return a manifest path relative to the project root (parent of the evolve dir).

    This keeps manifest paths stable regardless of the caller's working directory,
    so hooks invoked from a subdirectory still emit ``.evolve/entities/...`` paths.
    """
    path = Path(path)
    try:
        project_root = get_evolve_dir().resolve().parent
        return str(path.resolve().relative_to(project_root))
    except ValueError:
        return str(path)


def dedupe_manifest_entries(entries):
    """Return deterministically ordered manifest entries with exact dedupe."""
    normalized = []
    seen = set()
    for entry in sorted(entries, key=lambda item: (item["path"], item["type"], item["trigger"])):
        key = (entry["path"], entry["type"], entry["trigger"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entry)
    return normalized


def load_manifest(root_dir):
    """Load a frontmatter-only manifest from a recall root."""
    root_dir = Path(root_dir)
    entries = []
    for md in sorted(root_dir.glob("**/*.md")):
        if md.is_symlink() or ".git" in md.parts:
            continue

        entity = _parse_frontmatter_only(md)
        entity_type = entity.get("type")
        trigger = entity.get("trigger")
        if not entity_type or not trigger:
            continue

        entries.append(
            {
                "path": _manifest_path(md),
                "type": entity_type,
                "trigger": trigger,
            }
        )

    return dedupe_manifest_entries(entries)


# ---------------------------------------------------------------------------
# Bulk load / write
# ---------------------------------------------------------------------------


def load_all_entities(entities_dir):
    """Glob ``**/*.md`` under *entities_dir* and parse each file.

    Returns:
        list of entity dicts.
    """
    entities_dir = Path(entities_dir)
    entities = []
    for md in sorted(entities_dir.glob("**/*.md")):
        try:
            entity = markdown_to_entity(md)
            if entity.get("content"):
                entities.append(entity)
        except OSError:
            pass
    return entities


def write_entity_file(directory, entity, filename=None, overwrite=False):
    """Write a single entity as a markdown file under *directory*.

    The file is placed in a ``{type}/`` subdirectory.  Uses atomic
    write (write to ``.tmp``, then ``os.rename``).

    Args:
        directory: Entities root directory.
        entity: The entity dict to serialize.
        filename: Optional explicit slug for the target file (without the
            ``.md`` suffix). When omitted, the slug is derived from the
            entity content (the historical default).
        overwrite: When True, the entity is written to a deterministic
            ``{type}/{filename}.md`` path, overwriting any existing file in
            place (stable id, idempotent re-mirroring). When False (the
            default), the historical collision-avoiding behavior is kept —
            a ``-2``/``-3`` suffix is appended on collision.

    Returns:
        Path to the written file.
    """
    # Any non-empty type is accepted and used (sanitized) as the
    # subdirectory. An empty/invalid type falls back to "guideline".
    entity_type = sanitize_type(entity.get("type", "guideline")) or "guideline"
    entity["type"] = entity_type
    type_dir = Path(directory) / entity_type
    type_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(filename) if filename else slugify(entity.get("content", "entity"))
    content = entity_to_markdown(entity)

    # Write to a unique temp file first (avoids predictable .tmp collisions)
    fd, tmp_path = tempfile.mkstemp(dir=type_dir, suffix=".tmp", prefix=slug)
    target = None
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = None

        if overwrite:
            # Deterministic target: overwrite any existing file in place so
            # the entity id is stable across re-mirroring.
            target = type_dir / f"{slug}.md"
            os.replace(tmp_path, target)
            return target

        # Atomically claim the target using O_EXCL; retry on race
        while True:
            target = unique_filename(type_dir, slug)
            try:
                claim_fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(claim_fd)
                break
            except FileExistsError:
                continue

        os.replace(tmp_path, target)
        return target
    except BaseException:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        # Clean up the 0-byte placeholder if the replace didn't happen
        if target and os.path.exists(str(target)) and os.path.getsize(str(target)) == 0:
            os.unlink(str(target))
        raise
