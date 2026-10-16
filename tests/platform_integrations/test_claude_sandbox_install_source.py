"""The Claude sandbox e2e test mounts a copy of the installer's inputs, not the checkout.

``tests/e2e/test_claude_sandbox_learn_recall.py`` runs ``install.sh`` inside the
image from ``/repo`` and then gives Claude ``--dangerously-skip-permissions``
sessions with that mount still in place. These tests check, without docker,
that the copy it mounts keeps gitignored files and ``.git`` out, and still holds
everything the Claude install reads from a clone.
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_E2E_MODULE_PATH = _REPO_ROOT / "tests" / "e2e" / "test_claude_sandbox_learn_recall.py"
# Same convention as test_claude.py: a PATH with no claude binary on it.
_NO_CLAUDE_PATH = "/usr/bin:/bin"


def _load_e2e_module():
    spec = importlib.util.spec_from_file_location("claude_sandbox_e2e_under_test", _E2E_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.mark.platform_integrations
def test_install_source_copy_skips_ignored_files_and_git(tmp_path):
    """Tracked and untracked files are copied; gitignored ones, .git and other top-level dirs are not."""
    e2e = _load_e2e_module()
    repo = tmp_path / "checkout"
    (repo / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text("{}\n")
    (repo / "platform-integrations").mkdir()
    (repo / "platform-integrations" / "install.sh").write_text("#!/bin/bash\n")
    (repo / "platform-integrations" / "local-secret.env").write_text("ANTHROPIC_API_KEY=canary\n")
    (repo / ".env").write_text("ANTHROPIC_API_KEY=canary\n")
    (repo / "README.md").write_text("not needed\n")
    (repo / ".gitignore").write_text(".env\n*.env\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    # An uncommitted, untracked file under platform-integrations/ is what a
    # developer testing a local plugin edit would have; it must be copied.
    (repo / "platform-integrations" / "new_script.py").write_text("print('hi')\n")

    dest = tmp_path / "repo"
    e2e._copy_install_source(dest, repo_root=repo)

    copied = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert copied == [
        ".claude-plugin/marketplace.json",
        "platform-integrations/install.sh",
        "platform-integrations/new_script.py",
    ]


@pytest.mark.platform_integrations
def test_install_source_copy_of_this_checkout_installs_claude(tmp_path):
    """The installer, run from the copy of this checkout, registers the copy as a local marketplace and delivers every file.

    The claude CLI is kept off PATH, so this covers everything up to
    ``claude plugin marketplace add``; the e2e preflight covers the CLI step.
    """
    e2e = _load_e2e_module()
    dest = tmp_path / "repo"
    e2e._copy_install_source(dest)
    assert sorted(p.name for p in dest.iterdir()) == sorted(e2e.INSTALL_SOURCE_PATHS)

    project = tmp_path / "workspace"
    project.mkdir()
    result = subprocess.run(
        ["bash", str(dest / "platform-integrations" / "install.sh"), "install", "--platform", "claude", "--dir", str(project)],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path / "sandbox_home"), "PATH": _NO_CLAUDE_PATH},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Without .claude-plugin/ in the copy the installer would fall back to GitHub.
    assert f"Marketplace source: {dest.resolve()} (local)" in result.stdout, result.stdout

    assert e2e.CLAUDE_IMPORT_LINE in (project / "CLAUDE.md").read_text().splitlines()
    assert (project / ".evolve" / "EVOLVE.md").is_file()
    evolve_lite = tmp_path / "sandbox_home" / ".claude" / "evolve-lite"
    for name in ("audit_recall.py", "adapt_memory.py", "lib/evolve-lite/entity_io.py"):
        installed = evolve_lite / name
        assert installed.is_file() and installed.stat().st_size > 0, f"{installed} missing or empty"


@pytest.mark.platform_integrations
def test_docker_runs_mount_the_copy_not_the_checkout(tmp_path):
    """Every ``docker run`` mounts the per-test copy at /repo, and nothing mounts the checkout."""
    e2e = _load_e2e_module()
    workspace = tmp_path / "workspace"
    cmd = e2e._docker_run_prefix(workspace)
    mounts = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-v"]
    assert f"{e2e._install_source(workspace)}:/repo:ro" in mounts
    checkout = str(e2e.REPO_ROOT)
    assert not any(m.split(":", 1)[0] == checkout for m in mounts), mounts
