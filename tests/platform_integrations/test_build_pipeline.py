"""Tests for plugin-source/build_plugins.py — the plugin source compilation pipeline.

The build pipeline has three observable contracts that these tests pin down:

  1. Render is the inverse of check: rendering plugin-source/ into a tree, then
     running check_drift against that tree, returns 0.
  2. Render is hermetic: each platform's plugin_root is wiped before write, so
     a stale orphan in platform-integrations/<platform>/ is gone after render.
  3. Render is deterministic: rendering twice into the same tree produces
     byte-identical output.

Plus targeted tests for the routing conventions (`_<platform>/` prefix, bob's
1:1 commands generation, the per-platform Jinja context). Where order-sensitive
behavior used to slip in (e.g. "pick the alphabetically-first verbatim entry"),
these tests now address files by name so they don't break when the file tree
shifts.

Refs #219.
"""

from __future__ import annotations

import filecmp
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILD_SCRIPT = REPO_ROOT / "plugin-source" / "build_plugins.py"


def _import_build_module():
    spec = importlib.util.spec_from_file_location("_build_plugins_under_test", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_module():
    return _import_build_module()


@pytest.fixture
def isolated_repo(tmp_path, build_module, monkeypatch):
    """Copy plugin-source/ into tmp_path and monkeypatch REPO_ROOT / PLUGIN_SOURCE_DIR
    so render_to and check_drift operate against an isolated tree. Returns tmp_path."""
    shutil.copytree(REPO_ROOT / "plugin-source", tmp_path / "plugin-source")
    monkeypatch.setattr(build_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(build_module, "PLUGIN_SOURCE_DIR", tmp_path / "plugin-source")
    return tmp_path


@pytest.fixture
def rendered_repo(isolated_repo, build_module):
    """isolated_repo + a fresh render — for tests that inspect rendered output."""
    build_module.render_to(isolated_repo)
    return isolated_repo


def _plugin_root(manifest, platform: str) -> Path:
    """Absolute path of the platform's plugin_root from the (possibly
    monkeypatched) manifest. After `isolated_repo` patches `build_module.REPO_ROOT`,
    this already points into the test's tmp_path."""
    return Path(manifest.platforms[platform].plugin_root)


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestManifest:
    """Manifest sanity — purely structural, no I/O on platform-integrations/."""

    def test_manifest_loads_without_error(self, build_module):
        manifest = build_module.load_manifest()
        assert manifest.platforms, "manifest declares no platforms"
        assert manifest.files, "manifest declares no files"

    def test_every_manifest_source_exists(self, build_module):
        manifest = build_module.load_manifest()
        for entry in manifest.files:
            assert entry.source.is_file(), f"manifest references missing source: {entry.source}"

    def test_every_target_platform_is_declared(self, build_module):
        manifest = build_module.load_manifest()
        declared = set(manifest.platforms)
        for entry in manifest.files:
            for platform in entry.platforms:
                assert platform in declared, f"unknown platform {platform!r} in entry {entry.source}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestRenderInverseOfCheck:
    """The headline invariant: render then check is silent and returns 0."""

    def test_render_then_check_is_clean(self, isolated_repo, build_module, capsys):
        build_module.render_to(isolated_repo)
        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 0, f"check_drift returned {rc} on a fresh render. stderr:\n{captured.err}"
        assert captured.err == "", f"check_drift emitted output on a fresh render:\n{captured.err}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestRenderProperties:
    def test_render_is_idempotent(self, isolated_repo, build_module):
        """Rendering twice into the same tree must produce byte-identical output."""
        build_module.render_to(isolated_repo)
        first = {p.relative_to(isolated_repo): p.read_bytes() for p in (isolated_repo / "platform-integrations").rglob("*") if p.is_file()}
        build_module.render_to(isolated_repo)
        second = {p.relative_to(isolated_repo): p.read_bytes() for p in (isolated_repo / "platform-integrations").rglob("*") if p.is_file()}
        assert first.keys() == second.keys(), "second render produced a different file set"
        for path, body in first.items():
            assert body == second[path], f"non-deterministic output: {path}"

    def test_render_wipes_orphans_under_each_plugin_root(self, isolated_repo, build_module):
        """Stale files under any platform's plugin_root must be removed before write."""
        manifest = build_module.load_manifest()
        orphans = []
        for platform in manifest.platforms:
            root = _plugin_root(manifest, platform)
            root.mkdir(parents=True, exist_ok=True)
            orphan = root / "leftover-orphan.txt"
            orphan.write_text("stale content")
            orphans.append(orphan)

        build_module.render_to(isolated_repo)

        for orphan in orphans:
            assert not orphan.exists(), f"render did not wipe orphan {orphan}"

    def test_every_non_excluded_target_is_rendered(self, rendered_repo, build_module):
        """For every (file, platform) declared by the manifest that the platform
        doesn't exclude, the rendered output exists at the expected path."""
        manifest = build_module.load_manifest()
        for entry in manifest.files:
            for platform in entry.platforms:
                cfg = manifest.platforms[platform]
                if cfg.excludes(entry.target_rel):
                    continue
                rendered = _plugin_root(manifest, platform) / cfg.rewrite_target(entry.target_rel)
                assert rendered.is_file(), f"render did not emit {rendered}"

    def test_verbatim_files_match_source_byte_for_byte(self, rendered_repo, build_module):
        """Non-template files are copied byte-for-byte (excludes-aware)."""
        manifest = build_module.load_manifest()
        for entry in manifest.files:
            if build_module._is_template(entry.source):
                continue
            for platform in entry.platforms:
                cfg = manifest.platforms[platform]
                if cfg.excludes(entry.target_rel):
                    continue
                rendered = _plugin_root(manifest, platform) / cfg.rewrite_target(entry.target_rel)
                assert filecmp.cmp(entry.source, rendered, shallow=False), f"verbatim mismatch at {rendered}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestPerPlatformRouting:
    """Files under plugin-source/_<platform>/ ship only to that platform, and the
    `_<platform>/` prefix is stripped from the output target."""

    def test_underscore_platform_files_route_to_only_that_platform(self, build_module):
        manifest = build_module.load_manifest()
        for src, platforms in build_module._walk_sources():
            rel = src.relative_to(build_module.PLUGIN_SOURCE_DIR)
            head = rel.parts[0]
            if head.startswith("_") and head[1:] in manifest.platforms:
                expected = (head[1:],)
                assert platforms == expected, f"{rel} routes to {platforms}, expected {expected}"

    def test_underscore_platform_prefix_stripped_from_output(self, rendered_repo, build_module):
        """A file at _<platform>/<rest> renders to <plugin_root>/<rest>, not <plugin_root>/_<platform>/<rest>."""
        manifest = build_module.load_manifest()
        for src, platforms in build_module._walk_sources():
            rel = src.relative_to(build_module.PLUGIN_SOURCE_DIR)
            head = rel.parts[0]
            if not (head.startswith("_") and head[1:] in manifest.platforms):
                continue
            (platform,) = platforms
            target_rel = build_module._target_for(src)
            rendered = _plugin_root(manifest, platform) / target_rel
            assert rendered.is_file(), f"per-platform source {src} did not render to {rendered}"
            # And nothing under a `_<platform>/` subpath should appear in the output.
            stray = _plugin_root(manifest, platform) / head
            assert not stray.exists(), f"render leaked the `_<platform>/` prefix into {stray}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestBobCommandGeneration:
    """Bob commands are auto-generated 1:1 from the skills walk; description is
    pulled from each skill's SKILL.md.j2 frontmatter and the body uses the
    dash-form folder reference (since bob resolves skills by folder name)."""

    def _bob_commands_dir(self, rendered_repo, build_module) -> Path:
        manifest = build_module.load_manifest()
        return _plugin_root(manifest, "bob") / "commands"

    def test_one_command_per_skill(self, rendered_repo, build_module):
        skill_names = sorted(d.name for d in build_module._discover_skills())
        commands = sorted(p.stem.removeprefix("evolve-lite-") for p in self._bob_commands_dir(rendered_repo, build_module).glob("*.md"))
        assert commands == skill_names, "bob commands are not 1:1 with skills"

    def test_command_body_references_dash_form(self, rendered_repo, build_module):
        for cmd_file in self._bob_commands_dir(rendered_repo, build_module).glob("*.md"):
            skill = cmd_file.stem.removeprefix("evolve-lite-")
            body = cmd_file.read_text()
            assert f"`evolve-lite-{skill}`" in body, f"{cmd_file.name} body should reference the dash-form folder"
            assert f"evolve-lite:{skill}" not in body, f"{cmd_file.name} body should not use the colon form (bob resolves by folder)"

    def test_command_description_comes_from_skill_frontmatter(self, rendered_repo, build_module):
        for skill_dir in build_module._discover_skills():
            description = build_module._read_skill_description(skill_dir)
            cmd_file = self._bob_commands_dir(rendered_repo, build_module) / f"evolve-lite-{skill_dir.name}.md"
            assert f"description: {description}\n" in cmd_file.read_text()

    def test_command_frontmatter_has_no_name_field(self, rendered_repo, build_module):
        """Bob's command schema only honors `description` / `argument-hints`;
        an explicit `name:` would be silently ignored or rejected."""
        for cmd_file in self._bob_commands_dir(rendered_repo, build_module).glob("*.md"):
            text = cmd_file.read_text()
            # Frontmatter is the block between the first two `---` lines.
            _, frontmatter, _ = text.split("---", 2)
            assert "\nname:" not in frontmatter, f"{cmd_file.name} has a `name:` field bob doesn't support"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestCheckDrift:
    """Drift detection — pin specific failure modes by file name, not by index."""

    def test_committed_tree_is_clean(self, build_module, capsys):
        """The real committed platform-integrations/ matches a fresh render of plugin-source/."""
        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 0, f"check_drift returned {rc}. stderr:\n{captured.err}\nRun `just compile-plugins` and commit the result."

    def test_perturbed_template_is_detected_as_drift(self, rendered_repo, build_module, capsys):
        target = rendered_repo / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/learn/SKILL.md"
        assert target.is_file(), "test prerequisite missing — claude learn/SKILL.md not rendered"
        target.write_bytes(target.read_bytes() + b"\n# perturbation\n")

        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 1
        assert "drift:" in captured.err

    def test_perturbed_verbatim_file_is_detected_as_drift(self, rendered_repo, build_module, capsys):
        target = rendered_repo / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/learn/scripts/on_stop.py"
        assert target.is_file(), "test prerequisite missing — claude learn/scripts/on_stop.py not rendered"
        target.write_bytes(target.read_bytes() + b"\n# perturbation\n")

        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 1
        assert "drift:" in captured.err

    def test_perturbed_bob_command_is_detected_as_drift(self, rendered_repo, build_module, capsys):
        """Bob commands are generated, not source-tracked — their drift is also caught."""
        target = rendered_repo / "platform-integrations/bob/evolve-lite/commands/evolve-lite-learn.md"
        assert target.is_file(), "test prerequisite missing — bob's evolve-lite-learn command not rendered"
        target.write_bytes(target.read_bytes() + b"\n# perturbation\n")

        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 1
        assert "drift:" in captured.err

    def test_missing_rendered_file_is_detected(self, rendered_repo, build_module, capsys):
        target = rendered_repo / "platform-integrations/claude/plugins/evolve-lite/skills/evolve-lite/learn/SKILL.md"
        assert target.is_file()
        target.unlink()

        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 1
        assert "missing managed file:" in captured.err


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestJinjaTemplating:
    def test_template_renders_with_per_platform_context(self, rendered_repo, build_module):
        """A .j2 source rendered for two non-excluded platforms produces platform-specific output."""
        manifest = build_module.load_manifest()
        candidate = next(
            (
                e
                for e in manifest.files
                if build_module._is_template(e.source)
                and sum(1 for p in e.platforms if not manifest.platforms[p].excludes(e.target_rel)) >= 2
            ),
            None,
        )
        if candidate is None:
            pytest.skip("manifest has no templated source shipped to two non-excluded platforms")

        outputs = []
        for platform in candidate.platforms:
            cfg = manifest.platforms[platform]
            if cfg.excludes(candidate.target_rel):
                continue
            rendered = _plugin_root(manifest, platform) / cfg.rewrite_target(candidate.target_rel)
            outputs.append(rendered.read_bytes())

        assert any(a != b for a, b in zip(outputs, outputs[1:])), (
            "every platform produced the same bytes for a templated source — the .j2 file is not actually using its per-platform context"
        )
