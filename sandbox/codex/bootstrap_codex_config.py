#!/usr/bin/env python3
"""Ensure a container-friendly Codex config exists."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _matches_assignment(line: str, key: str) -> bool:
    return re.match(rf"^\s*{re.escape(key)}\s*=", line) is not None


def _matches_table(line: str, table_name: str) -> bool:
    return re.match(rf"^\s*\[{re.escape(table_name)}\]\s*(?:#.*)?$", line) is not None


def _is_table_header(line: str) -> bool:
    return re.match(r"^\s*\[", line) is not None


def _ensure_trailing_newline(lines: list[str], index: int) -> None:
    if 0 <= index < len(lines) and not lines[index].endswith("\n"):
        lines[index] += "\n"


def resolve_config_path(argv: list[str]) -> Path:
    if len(argv) > 2:
        raise SystemExit("usage: bootstrap_codex_config.py [config-path]")
    if len(argv) == 2:
        return Path(argv[1]).expanduser()

    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    return codex_home / "config.toml"


def ensure_top_level_setting(lines: list[str], key: str, value: str) -> bool:
    rendered = f'{key} = "{value}"\n'
    for index, line in enumerate(lines):
        if _is_table_header(line):
            break
        if _matches_assignment(line, key):
            if line.strip() == rendered.strip():
                return False
            lines[index] = rendered
            return True

    insert_at = 0
    for index, line in enumerate(lines):
        if _is_table_header(line):
            insert_at = index
            break
    else:
        insert_at = len(lines)

    if insert_at == len(lines):
        _ensure_trailing_newline(lines, insert_at - 1)
    lines.insert(insert_at, rendered)
    return True


def find_table(lines: list[str], table_name: str) -> tuple[int | None, int | None]:
    start = None
    end = None

    for index, line in enumerate(lines):
        if _matches_table(line, table_name):
            start = index
            continue
        if start is not None and _is_table_header(line):
            end = index
            break

    if start is not None and end is None:
        end = len(lines)

    return start, end


def ensure_feature_setting(lines: list[str], key: str, value: str) -> bool:
    start, end = find_table(lines, "features")
    rendered = f"{key} = {value}\n"

    if start is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend(["[features]\n", rendered])
        return True

    assert end is not None
    for index in range(start + 1, end):
        if _matches_assignment(lines[index], key):
            if lines[index].strip() == rendered.strip():
                return False
            lines[index] = rendered
            return True

    _ensure_trailing_newline(lines, end - 1)
    lines.insert(end, rendered)
    return True


def main(argv: list[str]) -> int:
    config_path = resolve_config_path(argv)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    else:
        lines = []

    changed = False
    changed |= ensure_top_level_setting(lines, "cli_auth_credentials_store", "file")
    changed |= ensure_feature_setting(lines, "codex_hooks", "true")

    if changed:
        content = "".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        config_path.write_text(content, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
