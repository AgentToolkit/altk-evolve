#!/usr/bin/env python3
"""Render plugin-source/ into platform-integrations/.

This script is the build pipeline for the unified plugin code tracked in
issue #219. It walks plugin-source/MANIFEST.toml, resolves each file entry
to its per-platform target path, and emits the rendered tree under
platform-integrations/.

Source files ending in `.j2` are rendered through Jinja2 with a per-platform
context (see PlatformConfig.context). Other files are copied verbatim.

Subcommands:
    render  — rewrite the managed files under platform-integrations/.
    check   — verify that committed platform-integrations/ matches a fresh
              render of plugin-source/. Exits non-zero on drift; used by the
              pre-commit hook and CI.
"""

from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SOURCE_DIR = REPO_ROOT / "plugin-source"
MANIFEST_PATH = PLUGIN_SOURCE_DIR / "MANIFEST.toml"

# Reserved manifest keys under [platforms.<name>]; everything else becomes Jinja2 context.
_RESERVED_PLATFORM_KEYS = frozenset({"plugin_root", "target_rewrites"})


@dataclass(frozen=True)
class TargetRewrite:
    pattern: re.Pattern[str]
    replacement: str


@dataclass(frozen=True)
class PlatformConfig:
    plugin_root: Path
    context: dict[str, Any]
    target_rewrites: tuple[TargetRewrite, ...] = ()

    def rewrite_target(self, target_rel: Path) -> Path:
        result = target_rel.as_posix()
        for rewrite in self.target_rewrites:
            result = rewrite.pattern.sub(rewrite.replacement, result)
        return Path(result)


@dataclass(frozen=True)
class FileEntry:
    source: Path
    target_rel: Path
    platforms: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    platforms: dict[str, PlatformConfig]
    files: tuple[FileEntry, ...]


def _default_target(entry: dict[str, Any]) -> str:
    """Resolve the target path for a manifest entry, defaulting from source.

    When `target` is omitted, fall back to `source` with a trailing `.j2`
    stripped (templates render to the same path minus the suffix).
    """
    target = entry.get("target")
    if isinstance(target, str):
        return target
    source: str = entry["source"]
    return source[:-3] if source.endswith(".j2") else source


def load_manifest() -> Manifest:
    raw = tomllib.loads(MANIFEST_PATH.read_text())
    platforms: dict[str, PlatformConfig] = {}
    for name, cfg in raw["platforms"].items():
        plugin_root = REPO_ROOT / cfg["plugin_root"]
        context = {key: val for key, val in cfg.items() if key not in _RESERVED_PLATFORM_KEYS}
        rewrites = tuple(
            TargetRewrite(pattern=re.compile(rw["pattern"]), replacement=rw["replacement"]) for rw in cfg.get("target_rewrites", [])
        )
        platforms[name] = PlatformConfig(plugin_root=plugin_root, context=context, target_rewrites=rewrites)
    all_platforms = tuple(platforms.keys())
    files = tuple(
        FileEntry(
            source=PLUGIN_SOURCE_DIR / entry["source"],
            target_rel=Path(_default_target(entry)),
            platforms=tuple(entry.get("platforms", all_platforms)),
        )
        for entry in raw.get("files", [])
    )
    for entry in files:
        if not entry.source.is_file():
            raise FileNotFoundError(f"manifest references missing source: {entry.source}")
        for platform in entry.platforms:
            if platform not in platforms:
                raise ValueError(f"manifest entry {entry.source} targets unknown platform '{platform}'")
    return Manifest(platforms=platforms, files=files)


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(PLUGIN_SOURCE_DIR)),
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )


def _render_template(env: Environment, source: Path, context: dict[str, Any]) -> bytes:
    rel = source.relative_to(PLUGIN_SOURCE_DIR).as_posix()
    template = env.get_template(rel)
    rendered = template.render(**context)
    return rendered.encode("utf-8")


def _is_template(path: Path) -> bool:
    return path.suffix == ".j2"


def render_to(out_root: Path) -> list[Path]:
    """Render every managed file into out_root/<plugin_root>/<target>.

    out_root is the prefix; the per-platform plugin_root from the manifest is
    appended. For an in-place build, pass REPO_ROOT.

    Returns the list of paths written, relative to out_root.
    """
    manifest = load_manifest()
    env = _jinja_env()
    written: list[Path] = []
    for entry in manifest.files:
        for platform in entry.platforms:
            cfg = manifest.platforms[platform]
            plugin_root_rel = cfg.plugin_root.relative_to(REPO_ROOT)
            target_rel = cfg.rewrite_target(entry.target_rel)
            target = out_root / plugin_root_rel / target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if _is_template(entry.source):
                ctx = {"platform": platform, **cfg.context}
                target.write_bytes(_render_template(env, entry.source, ctx))
            else:
                shutil.copy2(entry.source, target)
            written.append(plugin_root_rel / target_rel)
    return written


def check_drift() -> int:
    """Compare committed managed files against fresh-rendered content.

    Returns 0 if every managed file matches its source, 1 otherwise.
    """
    manifest = load_manifest()
    env = _jinja_env()
    drifts: list[tuple[Path, Path]] = []
    missing: list[Path] = []
    for entry in manifest.files:
        for platform in entry.platforms:
            cfg = manifest.platforms[platform]
            committed = cfg.plugin_root / cfg.rewrite_target(entry.target_rel)
            if not committed.is_file():
                missing.append(committed)
                continue
            if _is_template(entry.source):
                ctx = {"platform": platform, **cfg.context}
                rendered = _render_template(env, entry.source, ctx)
                if committed.read_bytes() != rendered:
                    drifts.append((entry.source, committed))
            else:
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
