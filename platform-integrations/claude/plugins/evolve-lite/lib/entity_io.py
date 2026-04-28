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

    Returns the existing recall roots. Only ``entities/`` is canonical —
    private entities live in ``entities/guideline/`` and shared entities
    live in ``entities/subscribed/{repo}/guideline/``.
    """
    evolve_dir = get_evolve_dir()
    candidates = [evolve_dir / "entities"]
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

_FRONTMATTER_KEYS = ("type", "trigger", "trajectory", "owner", "source", "visibility", "published_at")


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


def write_entity_file(directory, entity):
    """Write a single entity as a markdown file under *directory*.

    The file is placed in a ``{type}/`` subdirectory.  Uses atomic
    write (write to ``.tmp``, then ``os.rename``).

    Returns:
        Path to the written file.
    """
    _ALLOWED_TYPES = {"guideline", "preference"}
    entity_type = entity.get("type", "guideline")
    if not isinstance(entity_type, str) or entity_type not in _ALLOWED_TYPES:
        entity_type = "guideline"
    entity["type"] = entity_type
    type_dir = Path(directory) / entity_type
    type_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(entity.get("content", "entity"))
    content = entity_to_markdown(entity)

    # Write to a unique temp file first (avoids predictable .tmp collisions)
    fd, tmp_path = tempfile.mkstemp(dir=type_dir, suffix=".tmp", prefix=slug)
    target = None
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = None

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
