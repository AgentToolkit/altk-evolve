"""CI selection must retain inherited changes and fail closed on uncertain ranges."""

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("platform_test_selection", ROOT / "scripts/ci/platform_tests.py")
assert spec and spec.loader
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


@pytest.mark.parametrize(
    "path",
    [
        "platform-integrations/install.sh",
        "plugin-source/lib/entity_io.py",
        "tests/platform_integrations/test_publish.py",
        "tests/conftest.py",
        "pyproject.toml",
        "uv.lock",
        ".github/actions/run-tests/action.yaml",
        ".github/workflows/check-code.yaml",
        "scripts/ci/platform_tests.py",
        "packages/altk-evolve/pyproject.toml",
    ],
)
def test_shared_inputs_select_platform_tests(path):
    assert planner.relevant(path)


@pytest.mark.parametrize("path", ["README.md", "docs/index.md", "altk_evolve/backend/filesystem.py", "tests/unit/test_backend.py"])
def test_unrelated_changes_do_not_select_platform_tests(path):
    assert not planner.relevant(path)


def test_stack_diff_includes_inherited_changes_and_deleted_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "CI test")
    (tmp_path / "plugin-source").mkdir()
    (tmp_path / "plugin-source/old.txt").write_text("old")
    git("add", ".")
    git("commit", "-qm", "base")
    trunk = git("rev-parse", "HEAD")
    (tmp_path / "plugin-source/old.txt").unlink()
    git("add", "-u")
    git("commit", "-qm", "lower layer removes a plugin file")
    lower = git("rev-parse", "HEAD")
    (tmp_path / "README.md").write_text("upper layer documentation")
    git("add", ".")
    git("commit", "-qm", "upper layer")
    pr = {"base": {"sha": lower}, "head": {"sha": git("rev-parse", "HEAD")}}
    assert not planner.should_run("pull_request", {"pull_request": pr})
    pr["stack"] = {"base": {"sha": trunk}}
    assert planner.should_run("pull_request", {"pull_request": pr})


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "workflow_call", "release"])
def test_non_pr_runs_always_include_platform(event):
    assert planner.should_run(event, {})


def test_unavailable_or_malformed_base_runs_tests():
    assert planner.should_run("pull_request", {})
    assert planner.should_run("pull_request", {"pull_request": {"stack": {}}})
    pr = {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}

    def fail(*args):
        raise subprocess.CalledProcessError(128, "git")

    assert planner.should_run("pull_request", {"pull_request": pr}, diff=fail)
    pr["stack"] = {"base": {}}
    assert planner.should_run("pull_request", {"pull_request": pr}, diff=lambda *args: [])


@pytest.mark.parametrize(
    "selected,plan,regular,platform,success",
    [
        ("true", "success", "success", "success", True),
        ("false", "success", "success", "skipped", True),
        ("true", "success", "success", "skipped", False),
        ("true", "success", "success", "failure", False),
        ("true", "success", "success", "cancelled", False),
        ("false", "success", "failure", "skipped", False),
        ("false", "success", "cancelled", "skipped", False),
        ("", "failure", "success", "success", False),
        ("", "success", "success", "skipped", False),
    ],
)
def test_required_gate_only_accepts_success_or_planned_skip(selected, plan, regular, platform, success):
    workflow = yaml.safe_load((ROOT / ".github/workflows/check-code.yaml").read_text())
    script = workflow["jobs"]["run-tests"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, "PLAN_RESULT": plan, "PLATFORM_SELECTED": selected, "REGULAR_RESULT": regular, "PLATFORM_RESULT": platform},
        capture_output=True,
    )
    assert (result.returncode == 0) is success
