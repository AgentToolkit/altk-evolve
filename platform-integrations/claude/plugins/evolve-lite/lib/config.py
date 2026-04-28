"""Shared config reader/writer for evolve.config.yaml (project root).

pyyaml is not assumed to be installed. This module implements a minimal
YAML reader/writer that handles the flat and single-level-nested structures
used by evolve-lite config files (scalars and lists of scalar-valued dicts).
"""

import pathlib
import re
import sys


VALID_SCOPES = ("read", "write")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# Minimal YAML helpers (no pyyaml dependency)
# ---------------------------------------------------------------------------


def _strip_comments(line):
    """Strip a YAML inline comment, preserving '#' inside single/double quotes."""
    quote = None
    escape = False
    for i, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if quote:
            if ch == "\\" and quote == '"':
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "#":
            return line[:i].rstrip()
    return line.rstrip()


def _parse_block(lines, start, parent_indent):
    """Parse an indented block starting at `start`.

    Returns (value, next_index) where value is either:
    - a list (if block starts with '- ')
    - a dict (if block contains 'key: value' pairs at the same indent)

    parent_indent is the indent level of the parent key line.
    """
    i = start
    # Peek ahead to determine type: list or mapping
    # Skip blank lines first
    while i < len(lines):
        stripped = _strip_comments(lines[i])
        if stripped.strip():
            break
        i += 1
    if i >= len(lines):
        return {}, i

    first_content = _strip_comments(lines[i])
    block_indent = len(first_content) - len(first_content.lstrip())

    if block_indent <= parent_indent:
        # Nothing actually indented under this key
        return {}, i

    if first_content.strip().startswith("- "):
        # List
        items = []
        while i < len(lines):
            raw = _strip_comments(lines[i])
            if not raw.strip():
                i += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip())
            if cur_indent < block_indent:
                break
            content = raw.strip()
            if content.startswith("- "):
                item_text = content[2:].strip()
                if ":" in item_text:
                    item_dict = {}
                    ik, _, iv = item_text.partition(":")
                    item_dict[ik.strip()] = _cast(iv.strip())
                    i += 1
                    # Collect more keys at deeper indent for this list item
                    while i < len(lines):
                        cont = _strip_comments(lines[i])
                        if not cont.strip():
                            i += 1
                            continue
                        cont_indent = len(cont) - len(cont.lstrip())
                        if cont_indent <= cur_indent:
                            break
                        cont_content = cont.strip()
                        if ":" in cont_content:
                            ck, _, cv = cont_content.partition(":")
                            item_dict[ck.strip()] = _cast(cv.strip())
                        i += 1
                    items.append(item_dict)
                else:
                    items.append(_cast(item_text))
                    i += 1
            else:
                i += 1
        return items, i
    else:
        # Nested mapping
        mapping = {}
        while i < len(lines):
            raw = _strip_comments(lines[i])
            if not raw.strip():
                i += 1
                continue
            cur_indent = len(raw) - len(raw.lstrip())
            if cur_indent < block_indent:
                break
            content = raw.strip()
            if ":" in content:
                k, _, v = content.partition(":")
                k = k.strip()
                v = v.strip()
                if v:
                    mapping[k] = _cast(v)
                    i += 1
                else:
                    # nested further — recurse
                    nested, i = _parse_block(lines, i + 1, cur_indent)
                    mapping[k] = nested
            else:
                i += 1
        return mapping, i


def _parse_yaml(text):
    """Parse a minimal YAML subset into a Python dict.

    Supports:
    - Top-level ``key: value`` scalar pairs
    - Top-level ``key:`` with indented nested mappings or list items
    - Comments (#) are stripped
    """
    result = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = _strip_comments(line)
        if not stripped.strip():
            i += 1
            continue
        indent = len(stripped) - len(stripped.lstrip())
        if indent > 0:
            # Skip lines that belong to a block we already consumed
            i += 1
            continue
        key, sep, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            i += 1
            continue
        if value:
            result[key] = _cast(value)
            i += 1
        else:
            # Block value (list or nested mapping)
            block_val, i = _parse_block(lines, i + 1, 0)
            result[key] = block_val
    return result


def _cast(value):
    """Cast a YAML scalar string to an appropriate Python type.

    Quoted scalars stay strings — that's the whole point of YAML quoting.
    Only unquoted scalars get coerced to bool / null / int / float / list.
    """
    # Quoted: return the string verbatim (with single-quote unescaping).
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        stripped = value[1:-1]
        if value.startswith("'"):
            stripped = stripped.replace("''", "'")
        return stripped

    if value in ("true", "True", "yes"):
        return True
    if value in ("false", "False", "no"):
        return False
    if value in ("null", "~", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # Empty list literal
    if value == "[]":
        return []
    return value


def _dump_yaml(obj, indent=0):
    """Serialize a Python dict/list to a minimal YAML string."""
    lines = []
    prefix = "  " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict):
                lines.append(f"{prefix}{k}:")
                lines.extend(_dump_yaml(v, indent + 1).splitlines())
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{prefix}{k}: []")
                    continue
                lines.append(f"{prefix}{k}:")
                for item in v:
                    if isinstance(item, dict):
                        first = True
                        for ik, iv in item.items():
                            if first:
                                lines.append(f"{prefix}  - {ik}: {_scalar(iv)}")
                                first = False
                            else:
                                lines.append(f"{prefix}    {ik}: {_scalar(iv)}")
                    else:
                        lines.append(f"{prefix}  - {_scalar(item)}")
            else:
                lines.append(f"{prefix}{k}: {_scalar(v)}")
    return "\n".join(lines)


def _scalar(v):
    """Convert a Python value to a YAML scalar string, quoting when necessary."""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"

    # For non-string types, convert to string
    if not isinstance(v, str):
        return str(v)

    # Reserved YAML tokens that must be quoted
    reserved_tokens = {
        "true",
        "True",
        "TRUE",
        "false",
        "False",
        "FALSE",
        "null",
        "Null",
        "NULL",
        "~",
        "yes",
        "Yes",
        "YES",
        "no",
        "No",
        "NO",
        "on",
        "On",
        "ON",
        "off",
        "Off",
        "OFF",
    }

    # YAML indicator characters that require quoting
    yaml_indicators = set("-?:[]{},'&*#!|>'\"%@`")

    # Check if quoting is needed
    needs_quoting = (
        v in reserved_tokens  # Reserved token
        or v == ""  # Empty string
        or v[0] in " \t"
        or v[-1] in " \t"  # Leading/trailing whitespace
        or "#" in v  # Comment character
        or any(c in yaml_indicators for c in v)  # YAML special characters
        or v[0] in yaml_indicators  # Starts with indicator
    )

    if needs_quoting:
        # Use single quotes and escape embedded single quotes by doubling them
        escaped = v.replace("'", "''")
        return f"'{escaped}'"

    return v


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(project_root="."):
    """Read evolve.config.yaml from the project root and return a dict.

    Returns {} if the file does not exist.
    """
    path = pathlib.Path(project_root) / "evolve.config.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    return _parse_yaml(text)


def save_config(cfg, project_root="."):
    """Write *cfg* dict to evolve.config.yaml in the project root."""
    path = pathlib.Path(project_root) / "evolve.config.yaml"
    content = _dump_yaml(cfg)
    path.write_text(content + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Unified repo model (issue #217)
# ---------------------------------------------------------------------------


def _coerce_repo(entry):
    """Normalize a single repo dict. Returns None if required fields are missing."""
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    remote = entry.get("remote")
    if not isinstance(name, str) or not name.strip():
        return None
    if not is_valid_repo_name(name.strip()):
        print(
            f"evolve-lite: ignoring repo entry {name!r} — invalid name (only A-Z, a-z, 0-9, '.', '_', '-' allowed)",
            file=sys.stderr,
        )
        return None
    if not isinstance(remote, str) or not remote.strip():
        return None
    scope = entry.get("scope", "read")
    if isinstance(scope, str):
        scope = scope.strip()
    if scope not in VALID_SCOPES:
        print(
            f"evolve-lite: ignoring repo entry {name!r} — unknown scope {entry.get('scope')!r} (expected one of {', '.join(VALID_SCOPES)})",
            file=sys.stderr,
        )
        return None
    branch = entry.get("branch", "main")
    if not isinstance(branch, str) or not branch.strip():
        branch = "main"
    notes = entry.get("notes", "")
    if not isinstance(notes, str):
        notes = ""
    return {
        "name": name.strip(),
        "scope": scope,
        "remote": remote.strip(),
        "branch": branch.strip(),
        "notes": notes,
    }


def normalize_repos(cfg):
    """Return the unified ``repos`` list from *cfg* with invalid entries dropped.

    Invalid entries (missing ``name`` or ``remote``, duplicate names, unknown
    scopes) are silently skipped so callers can trust every returned dict.
    """
    if not isinstance(cfg, dict):
        return []
    raw = cfg.get("repos")
    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for entry in raw:
        repo = _coerce_repo(entry)
        if repo is None or repo["name"] in seen:
            continue
        seen.add(repo["name"])
        result.append(repo)
    return result


def get_repo(cfg, name):
    """Return the repo with the given name, or None."""
    for repo in normalize_repos(cfg):
        if repo.get("name") == name:
            return repo
    return None


def write_repos(cfg):
    """Return only the write-scope repos."""
    return [r for r in normalize_repos(cfg) if r.get("scope") == "write"]


def read_repos(cfg):
    """Return only the read-scope repos."""
    return [r for r in normalize_repos(cfg) if r.get("scope") == "read"]


def set_repos(cfg, repos):
    """Replace the ``repos`` list in-place with sanitized entries."""
    if not isinstance(cfg, dict):
        return cfg
    sanitized = []
    seen = set()
    for entry in repos or []:
        repo = _coerce_repo(entry)
        if repo is None or repo["name"] in seen:
            continue
        seen.add(repo["name"])
        sanitized.append(repo)
    cfg["repos"] = sanitized
    return cfg


def is_valid_repo_name(name):
    """Return True if *name* is safe to use as a repo / directory name.

    Rejects leading '-' so names can't be confused with git CLI flags when
    interpolated into clone paths.
    """
    if not isinstance(name, str):
        return False
    if name in (".", "..") or name.startswith("-"):
        return False
    return bool(_SAFE_NAME.match(name))


if __name__ == "__main__":
    # Quick self-test
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cfg = {
            "identity": {"user": "alice"},
            "repos": [
                {
                    "name": "memory",
                    "scope": "write",
                    "remote": "git@github.com:alice/evolve.git",
                    "branch": "main",
                    "notes": "public memory for foobar project",
                },
                {
                    "name": "bob",
                    "scope": "read",
                    "remote": "git@github.com:bob/evolve.git",
                    "branch": "main",
                    "notes": "",
                },
            ],
            "sync": {"on_session_start": True},
        }
        save_config(cfg, d)
        loaded = load_config(d)
        assert loaded["identity"]["user"] == "alice", loaded
        assert loaded["sync"]["on_session_start"] is True, loaded
        repos = normalize_repos(loaded)
        assert len(repos) == 2, repos
        assert repos[0]["scope"] == "write", repos
        assert repos[1]["name"] == "bob", repos
    print("config.py ok")
