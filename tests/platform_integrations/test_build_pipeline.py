"""Tests for scripts/build_plugins.py — the plugin source compilation pipeline.

These tests exercise the build pipeline end-to-end: render plugin-source/ into a
temp tree, verify each manifested file lands at its declared per-platform path,
and confirm the drift detector fires when the committed output is perturbed.

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
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_plugins.py"


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


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestManifest:
    def test_manifest_loads_without_error(self, build_module):
        manifest = build_module.load_manifest()
        assert manifest.platform_roots, "manifest declares no platforms"
        assert manifest.files, "manifest declares no files"

    def test_every_manifest_source_exists(self, build_module):
        manifest = build_module.load_manifest()
        for entry in manifest.files:
            assert entry.source.is_file(), f"manifest references missing source: {entry.source}"

    def test_every_manifest_target_platform_is_declared(self, build_module):
        manifest = build_module.load_manifest()
        declared = set(manifest.platform_roots)
        for entry in manifest.files:
            for platform in entry.platforms:
                assert platform in declared, f"unknown platform {platform!r} in entry {entry.source}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestRender:
    def test_render_into_temp_dir_matches_source(self, tmp_path, build_module):
        """Rendering into a fresh dir should produce an exact copy of every source file."""
        written = build_module.render_to(tmp_path)
        assert written, "render produced no output"
        manifest = build_module.load_manifest()
        for entry in manifest.files:
            for platform in entry.platforms:
                plugin_root_rel = manifest.platform_roots[platform].relative_to(REPO_ROOT)
                rendered = tmp_path / plugin_root_rel / entry.target_rel
                assert rendered.is_file(), f"render did not emit {rendered}"
                assert filecmp.cmp(entry.source, rendered, shallow=False), f"rendered file {rendered} differs from source {entry.source}"


@pytest.mark.platform_integrations
@pytest.mark.unit
class TestCheckDrift:
    def test_check_passes_on_clean_committed_tree(self, build_module, capsys):
        """The committed platform-integrations/ should match plugin-source/ at HEAD."""
        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 0, (
            f"check_drift returned {rc} on a clean tree. stderr:\n{captured.err}\n"
            f"This means committed platform-integrations/ has drifted from plugin-source/. "
            f"Run `just compile-plugins` and commit the result."
        )

    def test_check_fails_when_committed_file_is_perturbed(self, tmp_path, build_module, monkeypatch, capsys):
        """When a committed managed file has been edited, drift detection must fire.

        We simulate this by pointing the build script at a temp REPO_ROOT whose
        plugin-source/ matches the real one but whose platform-integrations/ has
        a perturbed copy of one managed file.
        """
        manifest = build_module.load_manifest()
        first_entry = manifest.files[0]
        first_platform = first_entry.platforms[0]
        plugin_root_rel = manifest.platform_roots[first_platform].relative_to(REPO_ROOT)

        fake_root = tmp_path / "fake_repo"
        fake_plugin_source = fake_root / "plugin-source"
        shutil.copytree(REPO_ROOT / "plugin-source", fake_plugin_source)

        committed = fake_root / plugin_root_rel / first_entry.target_rel
        committed.parent.mkdir(parents=True, exist_ok=True)
        committed.write_bytes(first_entry.source.read_bytes() + b"\n# perturbation\n")

        monkeypatch.setattr(build_module, "REPO_ROOT", fake_root)
        monkeypatch.setattr(build_module, "PLUGIN_SOURCE_DIR", fake_plugin_source)
        monkeypatch.setattr(build_module, "MANIFEST_PATH", fake_plugin_source / "MANIFEST.toml")

        rc = build_module.check_drift()
        captured = capsys.readouterr()
        assert rc == 1, "check_drift should return 1 when a managed file is perturbed"
        assert "drift:" in captured.err, "drift message should be printed to stderr"
