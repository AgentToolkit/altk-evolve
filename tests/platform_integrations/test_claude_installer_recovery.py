"""Local installer recovery and CLI health reporting; no network or real CLI."""

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations


@pytest.fixture
def installer(monkeypatch):
    script = (Path(__file__).parents[2] / "platform-integrations/install.sh").read_text()
    match = re.search(r"<<'PYEOF'\n(.*)\nPYEOF", script, re.DOTALL)
    assert match
    monkeypatch.setattr("sys.argv", ["install.sh", "", "status"])
    namespace = {"__name__": "installer_test"}
    exec(compile(match.group(1), "install.sh:PYEOF", "exec"), namespace)
    return namespace


def test_interrupted_swap_restored_before_failed_copy(installer, tmp_path, monkeypatch):
    destination = tmp_path / "marketplace"
    old = tmp_path / "marketplace.old"
    old.mkdir()
    (old / "last-good").write_text("keep me")
    staging = tmp_path / "marketplace.staging"
    staging.mkdir()
    (staging / "incomplete").write_text("partial")

    def fail_copy(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(installer["shutil"], "copytree", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        installer["FileOps"]().replace_tree_subset(tmp_path / "source", ["plugin"], destination)
    assert (destination / "last-good").read_text() == "keep me"
    assert not staging.exists()


@pytest.mark.parametrize("sources", [["a", "a/b"], ["a/b", "a"], ["a", "a"]])
def test_overlapping_marketplace_sources_copy_once(installer, tmp_path, sources):
    import json

    source = tmp_path / "source"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / "a/b").mkdir(parents=True)
    (source / "a/b/file").write_text("plugin content")
    (source / ".claude-plugin/marketplace.json").write_text(json.dumps({"plugins": [{"source": item} for item in sources]}))
    paths = installer["_claude_marketplace_subset"](source)
    destination = tmp_path / "marketplace"
    installer["FileOps"]().replace_tree_subset(source, paths, destination)
    assert (destination / "a/b/file").read_text() == "plugin content"


@pytest.mark.parametrize(
    ("state", "detail", "expected"),
    [
        ("✔ enabled", "", "✓ enabled"),
        ("✘ disabled", "", "✗ disabled"),
        (
            "✘ failed to load",
            "    Error: Marketplace evolve-marketplace failed to load: cache-miss\n",
            "✗ failed to load: Marketplace evolve-marketplace failed to load: cache-miss",
        ),
    ],
)
def test_status_reports_actual_plugin_health(installer, tmp_path, monkeypatch, capsys, state, detail, expected):
    binary = tmp_path / "claude"
    output = f"  ❯ evolve-lite@evolve-marketplace\n    Status: {state}\n{detail}\n  ❯ other@marketplace\n    Status: ✔ enabled\n"
    binary.write_text("#!/bin/sh\ncat <<'OUTPUT'\n" + output + "OUTPUT\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    installer["ClaudeInstaller"](installer["FileOps"]()).status(tmp_path)
    assert f"evolve-lite plugin  : {expected}" in capsys.readouterr().out


def test_failed_cli_query_cannot_report_healthy(installer, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(installer["shutil"], "which", lambda name: "claude")
    monkeypatch.setattr(
        installer["subprocess"], "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "evolve-lite", "failed")
    )
    installer["ClaudeInstaller"](installer["FileOps"]()).status(tmp_path)
    assert "evolve-lite plugin  : ? (could not query)" in capsys.readouterr().out


def test_failed_swap_restores_active_marketplace_and_retry(installer, tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "plugin").mkdir(parents=True)
    (source / "plugin/new").write_text("new version")
    destination = tmp_path / "marketplace"
    destination.mkdir()
    (destination / "last-good").write_text("old version")
    staging = destination.with_name("marketplace.staging")
    replace = installer["os"].replace

    def fail_activation(src, dst):
        if Path(src) == staging:
            raise OSError("simulated activation failure")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(installer["os"], "replace", fail_activation)
        with pytest.raises(OSError, match="activation failure"):
            installer["FileOps"]().replace_tree_subset(source, ["plugin"], destination)
    assert (destination / "last-good").read_text() == "old version"
    installer["FileOps"]().replace_tree_subset(source, ["plugin"], destination)
    assert (destination / "plugin/new").read_text() == "new version"
    assert not (destination / "last-good").exists()
    assert not destination.with_name("marketplace.old").exists()


def test_failed_rollback_keeps_backup_and_original_error(installer, tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "plugin").mkdir(parents=True)
    (source / "plugin/new").write_text("new version")
    destination = tmp_path / "marketplace"
    destination.mkdir()
    (destination / "last-good").write_text("old version")
    old = destination.with_name("marketplace.old")
    replace = installer["os"].replace

    def fail_activation_and_rollback(src, dst):
        if Path(dst) == destination:
            raise OSError("rollback failed" if Path(src) == old else "activation failed")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(installer["os"], "replace", fail_activation_and_rollback)
        with pytest.raises(OSError, match="activation failed"):
            installer["FileOps"]().replace_tree_subset(source, ["plugin"], destination)
    assert (old / "last-good").read_text() == "old version"
    installer["FileOps"]().replace_tree_subset(source, ["plugin"], destination)
    assert (destination / "plugin/new").read_text() == "new version"
    assert not old.exists()
