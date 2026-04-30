#!/usr/bin/env python3
"""Render plugin-source/ into platform-integrations/.

This script is the build pipeline for the unified plugin code tracked in
issue #219. It walks plugin-source/ — every file is fanned out to every
platform — and emits the rendered tree under platform-integrations/.

Per-platform configuration (plugin_root, Jinja context, optional path
rewrites) is encoded in the PLATFORMS dict below. There is no separate
manifest file; the file tree under plugin-source/ IS the manifest, with
two reserved entries:

  _macros.j2   — imported by SKILL.md.j2 templates; not rendered standalone.
  README.md    — describes the source tree; not shipped.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_SOURCE_DIR = REPO_ROOT / "plugin-source"

# Files at plugin-source/ that are NOT shipped to any platform.
RESERVED_SOURCES = frozenset({"_macros.j2", "README.md"})

# Per-platform config. Each entry declares where rendered output lands
# (plugin_root, relative to REPO_ROOT), the Jinja2 context exposed to
# .j2 templates, and any (regex, replacement) rewrites applied to a
# file's target path under that platform.
PLATFORMS: dict[str, dict[str, Any]] = {
    "claude": {
        "plugin_root": "platform-integrations/claude/plugins/evolve-lite",
        "context": {
            "forked_context": True,
            "user_skills_dir": "~/.claude/skills",
            "save_example_script_root": "${CLAUDE_PLUGIN_ROOT}/skills",
        },
        "target_rewrites": [],
    },
    "claw-code": {
        "plugin_root": "platform-integrations/claw-code/plugins/evolve-lite",
        "context": {
            "user_skills_dir": "~/.claw/skills",
            "save_example_script_root": "~/.claw/skills",
        },
        "target_rewrites": [],
    },
    "codex": {
        "plugin_root": "platform-integrations/codex/plugins/evolve-lite",
        "context": {
            "user_skills_dir": "plugins/evolve-lite/skills",
            "save_example_script_root": "plugins/evolve-lite/skills",
        },
        "target_rewrites": [],
    },
    "bob": {
        "plugin_root": "platform-integrations/bob/evolve-lite",
        "context": {
            "user_skills_dir": ".bob/skills",
            "save_example_script_root": ".bob/skills",
        },
        # Bob has no plugin-namespace concept; skill folders are flat
        # under .bob/skills/. Collapse the source skills/evolve-lite/<name>/
        # layout to skills/evolve-lite-<name>/ for bob's render output.
        "target_rewrites": [(r"^skills/evolve-lite/([^/]+)/", r"skills/evolve-lite-\1/")],
    },
}


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


def _platforms() -> dict[str, PlatformConfig]:
    out: dict[str, PlatformConfig] = {}
    for name, cfg in PLATFORMS.items():
        rewrites = tuple(TargetRewrite(pattern=re.compile(pat), replacement=repl) for pat, repl in cfg.get("target_rewrites", []))
        out[name] = PlatformConfig(
            plugin_root=REPO_ROOT / cfg["plugin_root"],
            context=dict(cfg.get("context", {})),
            target_rewrites=rewrites,
        )
    return out


def _walk_sources() -> list[Path]:
    """Every file under plugin-source/ that should be rendered or copied.

    Excludes files in RESERVED_SOURCES at the source root.
    """
    sources: list[Path] = []
    for path in sorted(PLUGIN_SOURCE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PLUGIN_SOURCE_DIR)
        if len(rel.parts) == 1 and rel.parts[0] in RESERVED_SOURCES:
            continue
        sources.append(path)
    return sources


def _target_for(source: Path) -> Path:
    """Per-platform target_rel before any rewrite — source path with .j2 stripped."""
    rel = source.relative_to(PLUGIN_SOURCE_DIR)
    if rel.suffix == ".j2":
        rel = rel.with_suffix("")
    return rel


def load_manifest() -> Manifest:
    platforms = _platforms()
    all_platforms = tuple(platforms.keys())
    files = tuple(FileEntry(source=src, target_rel=_target_for(src), platforms=all_platforms) for src in _walk_sources())
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

    out_root is the prefix; the per-platform plugin_root from PLATFORMS is
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
