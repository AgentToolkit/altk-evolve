"""Shared config reader/writer for evolve.config.yaml (project root).

pyyaml is not assumed to be installed. This module implements a minimal
YAML reader/writer that handles the flat and single-level-nested structures
used by evolve-lite config files (scalars and lists of scalar-valued dicts).
"""
import pathlib


# ---------------------------------------------------------------------------
# Minimal YAML helpers (no pyyaml dependency)
# ---------------------------------------------------------------------------

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
        stripped = lines[i].split("#", 1)[0].rstrip()
        if stripped.strip():
            break
        i += 1
    if i >= len(lines):
        return {}, i

    first_content = lines[i].split("#", 1)[0].rstrip()
    block_indent = len(first_content) - len(first_content.lstrip())

    if block_indent <= parent_indent:
        # Nothing actually indented under this key
        return {}, i

    if first_content.strip().startswith("- "):
        # List
        items = []
        while i < len(lines):
            raw = lines[i].split("#", 1)[0].rstrip()
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
                        cont = lines[i].split("#", 1)[0].rstrip()
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
            raw = lines[i].split("#", 1)[0].rstrip()
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
        stripped = line.split("#", 1)[0].rstrip()
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
    """Cast a YAML scalar string to an appropriate Python type."""
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
    # Strip surrounding quotes
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
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
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    return str(v)


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


if __name__ == "__main__":
    # Quick self-test
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        cfg = {
            "identity": {"user": "alice"},
            "public_repo": {"remote": "git@github.com:alice/evolve.git", "branch": "main"},
            "subscriptions": [
                {"name": "bob", "remote": "git@github.com:bob/evolve.git", "branch": "main"}
            ],
            "sync": {"on_session_start": True},
        }
        save_config(cfg, d)
        loaded = load_config(d)
        assert loaded["identity"]["user"] == "alice", loaded
        assert loaded["sync"]["on_session_start"] is True, loaded
        assert isinstance(loaded["subscriptions"], list), loaded
        assert loaded["subscriptions"][0]["name"] == "bob", loaded
    print("config.py ok")
