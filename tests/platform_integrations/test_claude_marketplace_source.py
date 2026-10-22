"""
Tests for which marketplace path the Claude installer registers (#387).

Claude registers a local marketplace by path and reads the plugin from that path
at every session start. The documented one-liner

    curl -fsSL .../install.sh | bash -s -- install --platform claude

downloads the source into a temp dir that the installer deletes when it exits,
so registering that temp dir left `evolve-lite` unable to load ("cache-miss").
A downloaded install must instead register an installer-owned copy at
``~/.claude/evolve-lite/marketplace/``. An install from a clone keeps
registering the clone itself.

These tests drive the real piped path: install.sh is fed to ``bash -s`` on
stdin from a directory with no source tree nearby, a fake ``curl`` on PATH
serves a tarball built from this working tree, and a fake ``claude`` on PATH
records the arguments it was called with.
"""

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_INSTALL_SH = _REPO_ROOT / "platform-integrations" / "install.sh"
# What a GitHub source tarball contains that the Claude install reads.
_TARBALL_PARTS = (".claude-plugin", "platform-integrations", "plugin-source")
_PLUGIN_REL = Path("platform-integrations/claude/plugins/evolve-lite")

_FAKE_CURL = """#!/usr/bin/env bash
# Fake curl: record the URL and stream the tarball instead of hitting GitHub.
echo "$*" >> "$FAKE_CURL_LOG"
exec cat "$FAKE_TARBALL"
"""

_FAKE_CLAUDE = """#!/usr/bin/env python3
# Fake claude CLI: record argv as one JSON line and succeed.
import json, os, sys
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""


def _build_tarball(dest, mutate=None, tar_filter=None):
    """Build a GitHub-style source tarball (single top-level dir) from this tree.

    `mutate(top)` edits the staged tree before it is packed; `tar_filter` is
    passed to `TarFile.add(filter=...)` to adjust individual members."""
    staging = dest.parent / (dest.name + ".src")
    top = staging / "altk-evolve-main"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for part in _TARBALL_PARTS:
        shutil.copytree(_REPO_ROOT / part, top / part, ignore=ignore)
    if mutate is not None:
        mutate(top)
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(top, arcname=top.name, filter=tar_filter)
    shutil.rmtree(staging)
    return dest


@pytest.fixture(scope="module")
def source_tarball(tmp_path_factory):
    return _build_tarball(tmp_path_factory.mktemp("tarball") / "altk-evolve-main.tar.gz")


class FakeCli:
    """A fake `curl` + `claude` on PATH, with their call logs."""

    def __init__(self, root, tarball):
        self.bin = root / "fakebin"
        self.bin.mkdir()
        for name, body in (("curl", _FAKE_CURL), ("claude", _FAKE_CLAUDE)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.curl_log = root / "curl.log"
        self.claude_log = root / "claude.log"
        # The installer's tempfile.mkdtemp() lands here, so a test can check it is gone.
        self.tmpdir = root / "tmp"
        self.tmpdir.mkdir()
        self.tarball = tarball

    def env(self):
        return {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "TMPDIR": str(self.tmpdir),
            "FAKE_TARBALL": str(self.tarball),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CLAUDE_LOG": str(self.claude_log),
        }

    def claude_calls(self):
        if not self.claude_log.exists():
            return []
        return [json.loads(line) for line in self.claude_log.read_text().splitlines()]

    def marketplace_adds(self):
        return [call[3] for call in self.claude_calls() if call[:3] == ["plugin", "marketplace", "add"]]


@pytest.fixture
def fake_cli(tmp_path, source_tarball):
    return FakeCli(tmp_path, source_tarball)


@pytest.fixture
def piped_cwd(tmp_path):
    """A cwd with no platform-integrations/ in it or its parent, like a user's project."""
    cwd = tmp_path / "piped_cwd"
    cwd.mkdir()
    return cwd


def _run_piped(fake_cli, cwd, project_dir, command, *extra, expect_success=True):
    """Run install.sh exactly like `curl … | bash -s -- <command> …`."""
    result = subprocess.run(
        ["bash", "-s", "--", command, "--platform", "claude", "--dir", str(project_dir), *extra],
        input=_INSTALL_SH.read_text(),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=fake_cli.env(),
    )
    if expect_success:
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


def _marketplace_source_line(stdout):
    lines = [ln for ln in stdout.splitlines() if "Marketplace source:" in ln]
    assert len(lines) == 1, stdout
    return lines[0]


def _assert_loadable_marketplace(path):
    """`path` holds a marketplace Claude can load: manifest plus every plugin it points at."""
    manifest = path / ".claude-plugin" / "marketplace.json"
    assert manifest.is_file(), f"missing {manifest}"
    plugins = json.loads(manifest.read_text())["plugins"]
    assert plugins, "marketplace.json lists no plugins"
    for plugin in plugins:
        plugin_dir = (path / plugin["source"]).resolve()
        assert plugin_dir.is_relative_to(path.resolve())
        assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file(), f"plugin not copied: {plugin_dir}"
        assert (plugin_dir / "skills").is_dir()


def _snapshot(root):
    """Map every file under `root` to its bytes."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _copy_dir(home):
    return home / ".claude" / "evolve-lite" / "marketplace"


@pytest.mark.platform_integrations
class TestDownloadedInstallMarketplace:
    """curl | bash installs must register a marketplace that outlives the installer."""

    def test_registered_marketplace_survives_installer_exit(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        result = _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")

        # The piped path really downloaded (through the fake curl).
        assert "archive/refs/heads/main.tar.gz" in fake_cli.curl_log.read_text()
        # The download temp dir is cleaned up on exit, as before.
        assert list(fake_cli.tmpdir.iterdir()) == []

        adds = fake_cli.marketplace_adds()
        assert len(adds) == 1, fake_cli.claude_calls()
        registered = Path(adds[0])
        # The registered marketplace still exists after the installer exited…
        assert registered.is_dir(), f"registered marketplace {registered} is gone after the installer exited"
        _assert_loadable_marketplace(registered)
        # …and it is the installer-owned copy, not the deleted temp dir.
        assert registered == _copy_dir(sandbox_home)
        assert ["plugin", "install", "evolve-lite@evolve-marketplace"] in fake_cli.claude_calls()
        # The info line names the path that was actually registered.
        assert str(registered) in _marketplace_source_line(result.stdout)

    def test_copy_holds_only_what_the_marketplace_needs(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")

        copy_dir = _copy_dir(sandbox_home)
        assert sorted(p.name for p in copy_dir.iterdir()) == [".claude-plugin", "platform-integrations"]
        # Only the plugin dir marketplace.json points at, at its relative path.
        for parent, child in (
            ("platform-integrations", "claude"),
            ("platform-integrations/claude", "plugins"),
            ("platform-integrations/claude/plugins", "evolve-lite"),
        ):
            assert [p.name for p in (copy_dir / parent).iterdir()] == [child], copy_dir / parent
        # Same content as the source the tarball was built from.
        for src in (_REPO_ROOT / _PLUGIN_REL).rglob("*"):
            if src.is_file() and "__pycache__" not in src.parts:
                dst = copy_dir / _PLUGIN_REL / src.relative_to(_REPO_ROOT / _PLUGIN_REL)
                assert dst.read_bytes() == src.read_bytes(), dst

    def test_reinstall_replaces_copy_without_stale_files(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")
        copy_dir = _copy_dir(sandbox_home)
        assert copy_dir.is_dir()

        # Files an older version shipped that the new one no longer does.
        stale_skill = copy_dir / _PLUGIN_REL / "skills" / "evolve-lite" / "removed-skill" / "SKILL.md"
        stale_skill.parent.mkdir(parents=True)
        stale_skill.write_text("stale\n")
        stale_top = copy_dir / "stale.txt"
        stale_top.write_text("stale\n")
        edited = copy_dir / _PLUGIN_REL / "EVOLVE.md"
        edited.write_text("old version\n")

        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")

        assert not stale_skill.exists()
        assert not stale_skill.parent.exists()
        assert not stale_top.exists()
        assert edited.read_bytes() == (_REPO_ROOT / _PLUGIN_REL / "EVOLVE.md").read_bytes()
        _assert_loadable_marketplace(copy_dir)
        # Idempotent: the same stable path is registered both times, and no
        # staging leftovers remain next to it.
        assert fake_cli.marketplace_adds() == [str(copy_dir), str(copy_dir)]
        assert sorted(p.name for p in copy_dir.parent.iterdir()) == ["adapt_memory.py", "audit_recall.py", "lib", "marketplace"]

    def test_uninstall_removes_copy(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")
        copy_dir = _copy_dir(sandbox_home)
        assert copy_dir.is_dir()
        for suffix in (".staging", ".old"):
            leftover = copy_dir.with_name(copy_dir.name + suffix)
            leftover.mkdir()
            (leftover / "interrupted").write_text("partial install")

        _run_piped(fake_cli, piped_cwd, temp_project_dir, "uninstall")

        assert not copy_dir.exists()
        # Nothing else of ours is left, so ~/.claude/evolve-lite/ goes too.
        assert not copy_dir.parent.exists()
        calls = fake_cli.claude_calls()
        assert ["plugin", "uninstall", "evolve-lite"] in calls
        assert ["plugin", "marketplace", "remove", "evolve-marketplace"] in calls

    def test_uninstall_keeps_user_content_and_never_follows_a_symlink(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home, tmp_path):
        evolve_dir = sandbox_home / ".claude" / "evolve-lite"
        evolve_dir.mkdir(parents=True)
        user_file = evolve_dir / "my-notes.txt"
        user_file.write_text("mine\n")
        outside = tmp_path / "outside"
        (outside / ".claude-plugin").mkdir(parents=True)
        (outside / ".claude-plugin" / "marketplace.json").write_text("{}\n")
        _copy_dir(sandbox_home).symlink_to(outside, target_is_directory=True)

        _run_piped(fake_cli, piped_cwd, temp_project_dir, "uninstall")

        assert not os.path.lexists(_copy_dir(sandbox_home))
        assert (outside / ".claude-plugin" / "marketplace.json").is_file()
        assert user_file.read_text() == "mine\n"

    def test_dry_run_writes_nothing(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        result = _run_piped(fake_cli, piped_cwd, temp_project_dir, "install", "--dry-run")

        assert "DRY RUN" in result.stdout
        assert not fake_cli.curl_log.exists(), "dry-run must not download"
        assert fake_cli.claude_calls() == [], "dry-run must not run the claude CLI"
        assert not (sandbox_home / ".claude").exists()
        assert list(temp_project_dir.iterdir()) == []
        # It reports the stable path a real run would register, not GitHub.
        assert str(_copy_dir(sandbox_home)) in _marketplace_source_line(result.stdout)

    def test_dry_run_uninstall_keeps_copy(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")
        copy_dir = _copy_dir(sandbox_home)
        before = sorted(str(p.relative_to(copy_dir)) for p in copy_dir.rglob("*"))
        calls_before = fake_cli.claude_calls()

        result = _run_piped(fake_cli, piped_cwd, temp_project_dir, "uninstall", "--dry-run")

        assert f"remove dir  → {copy_dir}" in result.stdout
        assert sorted(str(p.relative_to(copy_dir)) for p in copy_dir.rglob("*")) == before
        assert fake_cli.claude_calls() == calls_before

    def test_plugin_source_escaping_marketplace_is_refused(self, tmp_path, piped_cwd, temp_project_dir, sandbox_home):
        def escape(top):
            manifest = top / ".claude-plugin" / "marketplace.json"
            data = json.loads(manifest.read_text())
            data["plugins"][0]["source"] = "../outside"
            manifest.write_text(json.dumps(data))

        cli = FakeCli(tmp_path, _build_tarball(tmp_path / "escape.tar.gz", escape))
        result = _run_piped(cli, piped_cwd, temp_project_dir, "install", expect_success=False)

        assert result.returncode != 0
        assert "must be a subdirectory of the marketplace" in result.stderr
        assert not _copy_dir(sandbox_home).exists()
        assert cli.marketplace_adds() == []

    def test_absolute_plugin_source_is_refused(self, tmp_path, piped_cwd, temp_project_dir, sandbox_home):
        """An absolute `source` is refused even when it names a real directory."""
        outside = tmp_path / "outside"
        (outside / ".claude-plugin").mkdir(parents=True)
        (outside / ".claude-plugin" / "plugin.json").write_text("{}\n")
        (outside / "skills").mkdir()

        def absolute(top):
            manifest = top / ".claude-plugin" / "marketplace.json"
            data = json.loads(manifest.read_text())
            data["plugins"][0]["source"] = str(outside)
            manifest.write_text(json.dumps(data))

        cli = FakeCli(tmp_path, _build_tarball(tmp_path / "absolute.tar.gz", absolute))
        result = _run_piped(cli, piped_cwd, temp_project_dir, "install", expect_success=False)

        assert result.returncode != 0
        assert "must be a subdirectory of the marketplace" in result.stderr
        assert not _copy_dir(sandbox_home).exists()
        assert cli.marketplace_adds() == []
        assert sorted(p.name for p in outside.iterdir()) == [".claude-plugin", "skills"]

    def test_stale_staging_from_a_crashed_run_is_cleared(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home):
        """Leftover `.staging` / `.old` dirs from an interrupted run don't block a reinstall."""
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")
        copy_dir = _copy_dir(sandbox_home)
        staging = copy_dir.with_name("marketplace.staging")
        old = copy_dir.with_name("marketplace.old")
        for leftover in (staging, old):
            (leftover / "half-copied").mkdir(parents=True)
            (leftover / "half-copied" / "stale.txt").write_text("stale\n")

        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")

        assert not staging.exists()
        assert not old.exists()
        assert not (copy_dir / "half-copied").exists()
        _assert_loadable_marketplace(copy_dir)
        assert fake_cli.marketplace_adds() == [str(copy_dir), str(copy_dir)]

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0, reason="root can read a mode-0 file, so the copy cannot be made to fail"
    )
    def test_failed_copy_keeps_previous_copy_registered(self, fake_cli, piped_cwd, temp_project_dir, sandbox_home, tmp_path):
        """A copy that fails part-way leaves the previous copy intact and registered, and no staging dir."""
        _run_piped(fake_cli, piped_cwd, temp_project_dir, "install")
        copy_dir = _copy_dir(sandbox_home)
        before = _snapshot(copy_dir)
        assert before

        # A newer tarball with a plugin file the installer cannot read. The file
        # sits in a skill dir that only the marketplace copy reads, so the
        # failure happens inside replace_tree_subset, after earlier files of the
        # new copy have already been staged.
        unreadable = "altk-evolve-main/" + str(_PLUGIN_REL / "skills" / "evolve-lite" / "save" / "zz-unreadable.txt")

        def add_file(top):
            (top / unreadable.split("/", 1)[1]).write_text("new version\n")

        def mode_zero(info):
            if info.name == unreadable:
                info.mode = 0
            return info

        fake_cli.tarball = _build_tarball(tmp_path / "unreadable.tar.gz", add_file, mode_zero)
        result = _run_piped(fake_cli, piped_cwd, temp_project_dir, "install", expect_success=False)

        assert result.returncode != 0, result.stdout
        assert "Failed to install claude" in result.stderr
        # The previous copy is untouched and is still the registered marketplace.
        assert _snapshot(copy_dir) == before
        _assert_loadable_marketplace(copy_dir)
        assert fake_cli.marketplace_adds() == [str(copy_dir)]
        # The partial new copy was cleaned up.
        assert not copy_dir.with_name("marketplace.staging").exists()
        assert not copy_dir.with_name("marketplace.old").exists()

    def test_download_without_marketplace_manifest_falls_back_to_github(self, tmp_path, piped_cwd, temp_project_dir, sandbox_home):
        """An older tag with no root marketplace.json keeps today's GitHub fallback."""
        cli = FakeCli(tmp_path, _build_tarball(tmp_path / "old.tar.gz", lambda top: shutil.rmtree(top / ".claude-plugin")))
        result = _run_piped(cli, piped_cwd, temp_project_dir, "install")

        assert cli.marketplace_adds() == ["AgentToolkit/altk-evolve"]
        assert "(GitHub)" in _marketplace_source_line(result.stdout)
        assert not _copy_dir(sandbox_home).exists()


@pytest.mark.platform_integrations
class TestCloneInstallMarketplace:
    """Installs from a local clone keep registering the clone itself."""

    def test_clone_install_registers_clone_path(self, fake_cli, install_runner, sandbox_home):
        result = install_runner.run("install", platform="claude", env=fake_cli.env())

        assert not fake_cli.curl_log.exists(), "a clone install must not download"
        assert fake_cli.marketplace_adds() == [str(_REPO_ROOT.resolve())]
        assert f"{_REPO_ROOT.resolve()}" in _marketplace_source_line(result.stdout)
        assert "(local)" in _marketplace_source_line(result.stdout)
        assert not _copy_dir(sandbox_home).exists()
