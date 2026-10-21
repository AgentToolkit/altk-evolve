"""Project removal must not disable other projects sharing host assets."""

import json
import re

import pytest


@pytest.mark.parametrize("platform", ["bob", "codex"])
def test_project_uninstall_preserves_other_project(platform, install_runner, tmp_path, sandbox_home):
    first = install_runner.project_dir
    second = tmp_path / "second"
    second.mkdir()
    install_runner.run("install", platform=platform)
    install_runner.project_dir = second
    install_runner.run("install", platform=platform)
    status_before = install_runner.run("status").stdout
    global_dir = sandbox_home / f".{platform}"
    before = {p.relative_to(global_dir): p.read_bytes() for p in global_dir.rglob("*") if p.is_file()}
    install_runner.project_dir = first
    install_runner.run("uninstall", platform=platform)
    assert {p.relative_to(global_dir): p.read_bytes() for p in global_dir.rglob("*") if p.is_file()} == before
    install_runner.project_dir = second
    result = install_runner.run("status")
    section = re.split(r"\n  (?=\S)", result.stdout.split("Bob (.bob/):" if platform == "bob" else "Codex:")[1])[0]
    assert section == re.split(r"\n  (?=\S)", status_before.split("Bob (.bob/):" if platform == "bob" else "Codex:")[1])[0]
    assert (second / (".bob/skills/evolve-lite-save" if platform == "bob" else "plugins/evolve-lite")).is_dir()
    install_runner.run("uninstall", platform=platform, global_cleanup=True, dry_run=True)
    assert {p.relative_to(global_dir): p.read_bytes() for p in global_dir.rglob("*") if p.is_file()} == before
    install_runner.run("uninstall", platform=platform, global_cleanup=True)
    assert not (global_dir / "evolve-lite/audit_recall.py").exists()
    if platform == "bob":
        assert not (global_dir / "rules/00-evolve-lite.md").exists()
        assert not json.loads((global_dir / "settings.json").read_text()).get("tools", {}).get("allowed")
    else:
        assert "evolve-lite:managed" not in (global_dir / "AGENTS.md").read_text()
    install_runner.run("uninstall", platform=platform, global_cleanup=True)


@pytest.mark.parametrize("platform", ["claude", "claw-code", "hermes", "all", None])
def test_global_requires_supported_explicit_platform(install_runner, platform):
    result = install_runner.run("uninstall", platform=platform, global_cleanup=True, expect_success=False)
    assert result.returncode != 0
    assert "--global requires --platform bob or --platform codex" in result.stderr
