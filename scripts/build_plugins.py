#!/usr/bin/env python3
"""Render plugin-source/ into platform-integrations/.

This script is the build pipeline for the unified plugin code tracked in
issue #219. It walks plugin-source/MANIFEST.toml, resolves each file entry
to its per-platform target path, and emits the rendered tree under
platform-integrations/.

The current implementation handles verbatim file copies. Jinja2 templating
and per-platform overlay logic land in subsequent commits.

Subcommands:
    render  — rewrite the managed files under platform-integrations/.
    check   — verify that committed platform-integrations/ matches a fresh
              render of plugin-source/. Exits non-zero on drift; used by the
              pre-commit hook and CI.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SOURCE_DIR = REPO_ROOT / "plugin-source"
MANIFEST_PATH = PLUGIN_SOURCE_DIR / "MANIFEST.toml"


@dataclass(frozen=True)
class FileEntry:
    source: Path
    target_rel: Path
    platforms: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    platform_roots: dict[str, Path]
    files: tuple[FileEntry, ...]


def load_manifest() -> Manifest:
    raw = tomllib.loads(MANIFEST_PATH.read_text())
    platform_roots = {name: REPO_ROOT / cfg["plugin_root"] for name, cfg in raw["platforms"].items()}
    files = tuple(
        FileEntry(
            source=PLUGIN_SOURCE_DIR / entry["source"],
            target_rel=Path(entry["target"]),
            platforms=tuple(entry["platforms"]),
        )
        for entry in raw.get("files", [])
    )
    for entry in files:
        if not entry.source.is_file():
            raise FileNotFoundError(f"manifest references missing source: {entry.source}")
        for platform in entry.platforms:
            if platform not in platform_roots:
                raise ValueError(f"manifest entry {entry.source} targets unknown platform '{platform}'")
    return Manifest(platform_roots=platform_roots, files=files)


def render_to(out_root: Path) -> list[Path]:
    """Render every managed file into out_root/<plugin_root>/<target>.

    out_root is the prefix; the per-platform plugin_root from the manifest is
    appended. For an in-place build, pass REPO_ROOT.

    Returns the list of paths written, relative to out_root.
    """
    manifest = load_manifest()
    written: list[Path] = []
    for entry in manifest.files:
        for platform in entry.platforms:
            plugin_root_abs = manifest.platform_roots[platform]
            plugin_root_rel = plugin_root_abs.relative_to(REPO_ROOT)
            target = out_root / plugin_root_rel / entry.target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry.source, target)
            written.append(plugin_root_rel / entry.target_rel)
    return written


def check_drift() -> int:
    """Compare committed managed files against fresh-rendered content.

    Returns 0 if every managed file matches its source, 1 otherwise.
    """
    manifest = load_manifest()
    drifts: list[tuple[Path, Path]] = []
    missing: list[Path] = []
    for entry in manifest.files:
        for platform in entry.platforms:
            plugin_root = manifest.platform_roots[platform]
            committed = plugin_root / entry.target_rel
            if not committed.is_file():
                missing.append(committed)
                continue
            if not filecmp.cmp(entry.source, committed, shallow=False):
                drifts.append((entry.source, committed))
    if missing or drifts:
        for path in missing:
            print(f"missing managed file: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        for src, dst in drifts:
            print(
                f"drift: {dst.relative_to(REPO_ROOT)} differs from {src.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
        print(
            "\nrun `just compile-plugins` to regenerate, then commit the result.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_render(_: argparse.Namespace) -> int:
    written = render_to(REPO_ROOT)
    for path in written:
        print(path)
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    return check_drift()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render", help="render plugin-source/ into platform-integrations/")
    sub.add_parser("check", help="verify committed output matches a fresh render")
    args = parser.parse_args(argv)
    if args.cmd == "render":
        return cmd_render(args)
    if args.cmd == "check":
        return cmd_check(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
