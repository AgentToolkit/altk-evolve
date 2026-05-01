#!/usr/bin/env python3
"""Render plugin-source/ into platform-integrations/.

This script is the build pipeline for the unified plugin code tracked in
issue #219. It walks plugin-source/ — files fan out to every platform by
default — and emits the rendered tree under platform-integrations/.

Per-platform configuration (plugin_root, Jinja context, optional path
rewrites, optional plugin.json metadata target) is encoded in the
PLATFORMS dict below. There is no separate manifest file; the file tree
under plugin-source/ IS the manifest, with these reserved entries that
live in plugin-source/ but are never shipped:

  _macros.j2        — imported by SKILL.md.j2 templates; not rendered standalone.
  README.md         — describes the source tree.
  build_plugins.py  — this script.
  plugin.toml       — canonical plugin metadata; projected to per-platform
                      plugin.json by metadata_emit functions, never copied.

Per-platform routing: any file living under `plugin-source/_<platform>/...`
ships to that platform only, and the `_<platform>/` prefix is stripped from
its output target. This is how single-platform artifacts (claude's
`hooks/hooks.json`, bob's `custom_modes.yaml`, the per-platform READMEs)
live alongside the universal sources without leaking to other hosts.

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
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field

PLUGIN_SOURCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PLUGIN_SOURCE_DIR.parent

# Files at plugin-source/ that are NOT shipped to any platform.
RESERVED_SOURCES = frozenset({"_macros.j2", "README.md", "build_plugins.py", "plugin.toml"})


# ----- plugin.toml schema ----------------------------------------------------
#
# Lenient pydantic models for plugin.toml. Only `name` and `version` under
# [plugin] are required; everything else has a sensible default. `extra="allow"`
# keeps unknown keys from raising — typos or platform tables we don't render
# yet pass through silently rather than breaking the build.

_LENIENT = ConfigDict(extra="allow", populate_by_name=True)


class Author(BaseModel):
    model_config = _LENIENT
    name: str | None = None


class PluginUrls(BaseModel):
    """Title-case keys in TOML (Homepage, Repository) per pyproject convention,
    lowercase attributes in Python."""

    model_config = _LENIENT
    homepage: str | None = Field(default=None, alias="Homepage")
    repository: str | None = Field(default=None, alias="Repository")


class Plugin(BaseModel):
    model_config = _LENIENT
    name: str
    version: str
    description: str | None = None
    long_description: str | None = None
    display_name: str | None = None
    license: str | None = None
    keywords: list[str] = []
    authors: list[Author] = []
    urls: PluginUrls = Field(default_factory=PluginUrls)


class ClaudeConfig(BaseModel):
    """No declared fields today — claude-code's plugin.json is fully covered by
    [plugin]. The table exists so users can land claude-only extras
    (e.g. `commands`, `mcpServers`, `userConfig`) under [claude] and have them
    flow into claude's plugin.json without leaking to other hosts."""

    model_config = _LENIENT


class ClawCodeConfig(BaseModel):
    model_config = _LENIENT
    default_enabled: bool | None = None


class CodexConfig(BaseModel):
    model_config = _LENIENT
    category: str | None = None
    capabilities: list[str] = []
    brand_color: str | None = None
    default_prompt: list[str] = []


class PluginMetadata(BaseModel):
    """Top-level shape of plugin-source/plugin.toml.

    Per-host tables hold platform-specific fields. Anything a user adds to
    [plugin] flows into every host's plugin.json top-level (host-agnostic
    metadata like `commands`, `agents`, `mcpServers`, etc. per the
    Claude Code plugin.json schema). Anything added to a host table flows
    only into that host's output ([codex] extras land in the codex
    `interface` block, where the rest of [codex] already lives)."""

    model_config = _LENIENT
    plugin: Plugin
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    claw_code: ClawCodeConfig = Field(default_factory=ClawCodeConfig, alias="claw-code")
    codex: CodexConfig = Field(default_factory=CodexConfig)


# ----- plugin.json output models --------------------------------------------
#
# Per-platform output schemas. Field declaration order is the JSON key order;
# `serialization_alias` maps snake_case attributes to the camelCase JSON
# spelling each host expects. Optional fields default to None and are dropped
# at serialize time via `exclude_none=True`, so unset metadata vanishes from
# the rendered plugin.json without any explicit "skip if empty" plumbing.
#
# Output models share `_LENIENT` (extra="allow") with the input schema:
# host-side plugin.json schemas evolve, and we want a future caller to be able
# to pass an unknown kwarg (or load an unknown TOML field through the input
# model) and have it round-trip into the rendered JSON without code changes
# here.

_SKILLS_PATH = "./skills/evolve-lite/"


class _OutAuthor(BaseModel):
    model_config = _LENIENT
    name: str


class _ClaudeOut(BaseModel):
    model_config = _LENIENT
    name: str
    version: str
    description: str | None = None
    author: _OutAuthor | None = None
    skills: str = _SKILLS_PATH


class _ClawCodeOut(BaseModel):
    model_config = _LENIENT
    name: str
    version: str
    description: str | None = None
    author: _OutAuthor | None = None
    default_enabled: bool | None = Field(default=None, serialization_alias="defaultEnabled")
    skills: str = _SKILLS_PATH


class _CodexInterfaceOut(BaseModel):
    model_config = _LENIENT
    display_name: str | None = Field(default=None, serialization_alias="displayName")
    short_description: str | None = Field(default=None, serialization_alias="shortDescription")
    long_description: str | None = Field(default=None, serialization_alias="longDescription")
    developer_name: str | None = Field(default=None, serialization_alias="developerName")
    category: str | None = None
    capabilities: list[str] | None = None
    website_url: str | None = Field(default=None, serialization_alias="websiteURL")
    default_prompt: list[str] | None = Field(default=None, serialization_alias="defaultPrompt")
    brand_color: str | None = Field(default=None, serialization_alias="brandColor")

    def or_none(self) -> "_CodexInterfaceOut | None":
        """Return self only when at least one field is populated; otherwise
        None, so the interface block disappears from the rendered JSON."""
        return self if self.model_dump(exclude_none=True) else None


class _CodexOut(BaseModel):
    model_config = _LENIENT
    name: str
    version: str
    description: str | None = None
    author: _OutAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] | None = None
    skills: str = _SKILLS_PATH
    interface: _CodexInterfaceOut | None = None


# ----- projection ------------------------------------------------------------
#
# Each platform that ships a plugin.json gets a small projection function that
# takes the validated PluginMetadata and returns its output model. The
# renderer serializes the model with `model_dump_json(by_alias=True,
# exclude_none=True, indent=2)`, which handles camelCase mapping and
# dropping-unset-fields uniformly.

MetadataEmit = Callable[["PluginMetadata"], BaseModel]


def _extras(model: BaseModel) -> dict[str, Any]:
    """The undeclared keys captured by `extra='allow'`. Empty dict if none."""
    return dict(model.__pydantic_extra__ or {})


def _author(plugin: Plugin) -> _OutAuthor | None:
    """Single-author hosts take authors[0]. Round-trips name plus any extra
    author fields the user set (email, url, ...) via model_validate."""
    if not plugin.authors or not plugin.authors[0].name:
        return None
    return _OutAuthor.model_validate(plugin.authors[0].model_dump(exclude_none=True))


def _claude_plugin_json(meta: PluginMetadata) -> _ClaudeOut:
    p = meta.plugin
    return _ClaudeOut(
        name=p.name,
        version=p.version,
        description=p.description,
        author=_author(p),
        **_extras(p),
        **_extras(meta.claude),
    )


def _claw_code_plugin_json(meta: PluginMetadata) -> _ClawCodeOut:
    p = meta.plugin
    return _ClawCodeOut(
        name=p.name,
        version=p.version,
        description=p.description,
        author=_author(p),
        default_enabled=meta.claw_code.default_enabled,
        **_extras(p),
        **_extras(meta.claw_code),
    )


def _codex_plugin_json(meta: PluginMetadata) -> _CodexOut:
    p = meta.plugin
    c = meta.codex
    return _CodexOut(
        name=p.name,
        version=p.version,
        description=p.description,
        author=_author(p),
        homepage=p.urls.homepage,
        repository=p.urls.repository,
        license=p.license,
        keywords=p.keywords or None,
        interface=_CodexInterfaceOut(
            display_name=p.display_name,
            short_description=p.description,
            long_description=p.long_description,
            developer_name=p.authors[0].name if p.authors else None,
            category=c.category,
            capabilities=c.capabilities or None,
            website_url=p.urls.homepage,
            default_prompt=c.default_prompt or None,
            brand_color=c.brand_color,
            **_extras(c),
        ).or_none(),
        **_extras(p),
    )


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
        "target_excludes": [],
        "metadata_target": ".claude-plugin/plugin.json",
        "metadata_emit": _claude_plugin_json,
    },
    "claw-code": {
        "plugin_root": "platform-integrations/claw-code/plugins/evolve-lite",
        "context": {
            "user_skills_dir": "~/.claw/skills",
            "save_example_script_root": "~/.claw/skills",
        },
        "target_rewrites": [],
        "target_excludes": [],
        # claw-code is a claude-code fork that reuses the .claude-plugin/ convention.
        "metadata_target": ".claude-plugin/plugin.json",
        "metadata_emit": _claw_code_plugin_json,
    },
    "codex": {
        "plugin_root": "platform-integrations/codex/plugins/evolve-lite",
        "context": {
            "user_skills_dir": "plugins/evolve-lite/skills",
            "save_example_script_root": "plugins/evolve-lite/skills",
        },
        "target_rewrites": [],
        "target_excludes": [],
        "metadata_target": ".codex-plugin/plugin.json",
        "metadata_emit": _codex_plugin_json,
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
        "target_excludes": [],
        # Bob has no plugin system, so no plugin.json is emitted. Bob's
        # commands/ directory is generated 1:1 from the skills walk by
        # _bob_command_targets(); no static command files exist in
        # plugin-source/.
        "metadata_target": None,
        "metadata_emit": None,
    },
}


# Bob's slash-command surface: one .md file per skill, generated from the
# skill folder name and its SKILL.md.j2 frontmatter `description`. Bob
# command frontmatter only honors `description` (and `argument-hints`,
# which our commands don't need); the slash-command identifier comes from
# the file name. The body references the skill by its on-disk folder name
# (`evolve-lite-<skill>`, dash form) — bob resolves skills by folder name,
# and folders stay colon-free for Windows compatibility.
_BOB_COMMAND_TEMPLATE = (
    "---\n"
    "description: {description}\n"
    "---\n"
    "Use the `evolve-lite-{skill}` skill on the current conversation. Follow the skill's instructions exactly.\n"
)


def _discover_skills() -> list[Path]:
    """Skill folders under plugin-source/skills/evolve-lite/ that ship a SKILL.md.j2."""
    skills_root = PLUGIN_SOURCE_DIR / "skills" / "evolve-lite"
    return sorted(p for p in skills_root.iterdir() if p.is_dir() and (p / "SKILL.md.j2").is_file())


def _read_skill_description(skill_dir: Path) -> str:
    """Pull the single-line `description:` value from a skill's SKILL.md.j2 frontmatter."""
    text = (skill_dir / "SKILL.md.j2").read_text(encoding="utf-8")
    match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"missing `description` in {skill_dir.name}/SKILL.md.j2")
    return match.group(1).strip()


def _bob_command_bytes(skill_dir: Path) -> bytes:
    return _BOB_COMMAND_TEMPLATE.format(
        skill=skill_dir.name,
        description=_read_skill_description(skill_dir),
    ).encode("utf-8")


def _bob_command_targets() -> list[tuple[Path, Path, bytes]]:
    """Triples of (skill_source_for_drift_label, target_rel_to_repo_root, content)
    for every bob command — one per skill — derived from the skills walk."""
    bob_root_rel = Path(PLATFORMS["bob"]["plugin_root"])
    out: list[tuple[Path, Path, bytes]] = []
    for skill_dir in _discover_skills():
        target_rel = bob_root_rel / "commands" / f"evolve-lite-{skill_dir.name}.md"
        out.append((skill_dir / "SKILL.md.j2", target_rel, _bob_command_bytes(skill_dir)))
    return out


@dataclass(frozen=True)
class TargetRewrite:
    pattern: re.Pattern[str]
    replacement: str


@dataclass(frozen=True)
class PlatformConfig:
    plugin_root: Path
    context: dict[str, Any]
    target_rewrites: tuple[TargetRewrite, ...] = ()
    target_excludes: tuple[re.Pattern[str], ...] = ()
    metadata_target: Path | None = None
    metadata_emit: MetadataEmit | None = None

    def rewrite_target(self, target_rel: Path) -> Path:
        result = target_rel.as_posix()
        for rewrite in self.target_rewrites:
            result = rewrite.pattern.sub(rewrite.replacement, result)
        return Path(result)

    def excludes(self, target_rel: Path) -> bool:
        """True if this platform should skip rendering `target_rel`.

        Patterns match the source-side target path (before any rewrite),
        so callers can write excludes against the plugin-source/ layout
        without needing to know each platform's rewrite rules.
        """
        s = target_rel.as_posix()
        return any(p.search(s) for p in self.target_excludes)


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
        excludes = tuple(re.compile(pat) for pat in cfg.get("target_excludes", []))
        metadata_target = cfg.get("metadata_target")
        out[name] = PlatformConfig(
            plugin_root=REPO_ROOT / cfg["plugin_root"],
            context=dict(cfg.get("context", {})),
            target_rewrites=rewrites,
            target_excludes=excludes,
            metadata_target=Path(metadata_target) if metadata_target else None,
            metadata_emit=cfg.get("metadata_emit"),
        )
    return out


def _load_metadata() -> PluginMetadata:
    """Parse and validate the canonical plugin.toml. Resolved against the live
    PLUGIN_SOURCE_DIR so test monkeypatching of the module global works the
    same way the source walk does."""
    with (PLUGIN_SOURCE_DIR / "plugin.toml").open("rb") as fp:
        raw = tomllib.load(fp)
    return PluginMetadata.model_validate(raw)


def _render_plugin_json(cfg: PlatformConfig, metadata: PluginMetadata) -> bytes:
    assert cfg.metadata_emit is not None
    model = cfg.metadata_emit(metadata)
    return (model.model_dump_json(by_alias=True, exclude_none=True, indent=2) + "\n").encode("utf-8")


def _walk_sources() -> list[tuple[Path, tuple[str, ...]]]:
    """Every source file paired with the platforms it ships to.

    Files under `plugin-source/_<platform>/...` ship to that single platform
    only; everything else fans out to every platform.

    Excludes files in RESERVED_SOURCES at the source root, and any path
    that traverses a __pycache__ directory (build_plugins.py running from
    plugin-source/ writes a sibling __pycache__/ that must not ship).
    """
    all_platforms = tuple(PLATFORMS.keys())
    platform_dirs = {f"_{name}": (name,) for name in PLATFORMS}
    sources: list[tuple[Path, tuple[str, ...]]] = []
    for path in sorted(PLUGIN_SOURCE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PLUGIN_SOURCE_DIR)
        if "__pycache__" in rel.parts:
            continue
        if len(rel.parts) == 1 and rel.parts[0] in RESERVED_SOURCES:
            continue
        platforms = platform_dirs.get(rel.parts[0], all_platforms)
        sources.append((path, platforms))
    return sources


def _target_for(source: Path) -> Path:
    """Per-platform target_rel before any rewrite — source path with the
    leading `_<platform>/` prefix and any `.j2` suffix stripped."""
    rel = source.relative_to(PLUGIN_SOURCE_DIR)
    if rel.parts and rel.parts[0].startswith("_") and rel.parts[0][1:] in PLATFORMS:
        rel = Path(*rel.parts[1:])
    if rel.suffix == ".j2":
        rel = rel.with_suffix("")
    return rel


def load_manifest() -> Manifest:
    platforms = _platforms()
    files = tuple(FileEntry(source=src, target_rel=_target_for(src), platforms=plats) for src, plats in _walk_sources())
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

    Each platform's plugin_root under out_root is wiped before writing, so
    files removed from plugin-source/ (renamed skills, deleted scripts,
    obsolete commands) cannot linger as orphans in the rendered tree.

    Returns the list of paths written, relative to out_root.
    """
    manifest = load_manifest()
    for cfg in manifest.platforms.values():
        plugin_root_rel = cfg.plugin_root.relative_to(REPO_ROOT)
        target_root = out_root / plugin_root_rel
        if target_root.exists():
            shutil.rmtree(target_root)
    env = _jinja_env()
    written: list[Path] = []
    for entry in manifest.files:
        for platform in entry.platforms:
            cfg = manifest.platforms[platform]
            if cfg.excludes(entry.target_rel):
                continue
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

    metadata = _load_metadata()
    for platform, cfg in manifest.platforms.items():
        if cfg.metadata_target is None:
            continue
        plugin_root_rel = cfg.plugin_root.relative_to(REPO_ROOT)
        target = out_root / plugin_root_rel / cfg.metadata_target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_render_plugin_json(cfg, metadata))
        written.append(plugin_root_rel / cfg.metadata_target)

    for _, target_rel, content in _bob_command_targets():
        target = out_root / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(target_rel)
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
            if cfg.excludes(entry.target_rel):
                continue
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

    plugin_toml = PLUGIN_SOURCE_DIR / "plugin.toml"
    metadata = _load_metadata()
    for platform, cfg in manifest.platforms.items():
        if cfg.metadata_target is None:
            continue
        committed = cfg.plugin_root / cfg.metadata_target
        if not committed.is_file():
            missing.append(committed)
            continue
        rendered = _render_plugin_json(cfg, metadata)
        if committed.read_bytes() != rendered:
            drifts.append((plugin_toml, committed))

    for skill_src, target_rel, content in _bob_command_targets():
        committed = REPO_ROOT / target_rel
        if not committed.is_file():
            missing.append(committed)
            continue
        if committed.read_bytes() != content:
            drifts.append((skill_src, committed))

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
