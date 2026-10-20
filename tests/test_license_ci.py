"""Regression tests for fail-closed license inventory, policy and release promotion."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/licenses" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load_script("check")
ci = load_script("ci")
POLICY = {"approved_expressions": ["MIT", "Apache-2.0", "Apache-2.0 WITH LLVM-exception"], "exceptions": []}
BASELINE = {"reviewed": True, "findings": []}


def report(licenses=None, name="demo", version="1"):
    pkg = {"Name": name, "Version": version}
    if licenses is not None:
        pkg["Licenses"] = licenses
    return {"SchemaVersion": 2, "Results": [{"Type": "python-pkg", "Packages": [pkg]}]}


def run_report(data, **kwargs):
    return check.evaluate(
        data,
        kwargs.pop("installed", []),
        kwargs.pop("policy", POLICY),
        kwargs.pop("baseline", BASELINE),
        "python-core",
        kwargs.pop("mode", "enforce"),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("expression", "approved"),
    [
        ("MIT", True),
        ("MIT OR GPL-3.0-only", True),
        ("MIT AND GPL-3.0-only", False),
        ("(MIT OR GPL-3.0-only) AND Apache-2.0", True),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("MIT WITH made-up-exception", False),
        ("MIT OR", False),
        ("MIT OR (GPL", False),
        ("MIT garbage", False),
        ("MIT/Proprietary", False),
        ("", False),
        ("MIT WITH OR", False),
    ],
)
def test_spdx_expression(expression, approved):
    assert check.approved_expression(expression, set(POLICY["approved_expressions"])) is approved


def test_missing_metadata_is_a_finding_even_without_trivy_severity():
    result, failed = run_report(report())
    assert failed
    assert result["findings"][0]["issue"] == "missing-license"


def test_report_mode_reports_findings_without_blocking():
    result, failed = run_report(report(["LGPL-3.0-only"]), mode="report")
    assert result["pending"] == 1
    assert not failed


def test_known_permissive_license_passes():
    result, failed = run_report(report(["MIT"]))
    assert result["pending"] == 0
    assert not failed


def test_enforcement_requires_reviewed_baseline():
    with pytest.raises(ValueError, match="reviewed Linux baseline"):
        run_report(report(), baseline={"reviewed": False})


@pytest.mark.parametrize("data", [{}, {"SchemaVersion": 2, "Results": []}])
def test_incomplete_scan_fails_even_in_report_mode(data):
    with pytest.raises(ValueError):
        run_report(data, mode="report")


def test_independent_inventory_detects_analyzer_coverage_gap():
    with pytest.raises(ValueError, match="missing from Trivy"):
        run_report(report(["MIT"]), installed=[{"name": "unseen", "version": "1", "files": []}], mode="report")


def test_missing_shipped_license_files_requires_review():
    result, failed = run_report(report(["MIT"]), installed=[{"name": "demo", "version": "1", "files": []}])
    assert failed
    assert result["findings"][0]["issue"] == "missing-license-files"


def test_reviewed_baseline_is_bound_to_version_scope_and_evidence():
    original = report(["LGPL-3.0-only"])
    installed = [{"name": "demo", "version": "1", "files": [{"path": "LICENSE", "sha256": "first"}]}]
    initial, _ = run_report(original, installed=installed)
    entry = {**initial["findings"][0], "reason": "Reviewed LGPL obligations", "reviewer": "maintainer"}
    baseline = {"reviewed": True, "findings": [entry]}
    assert not run_report(original, installed=installed, baseline=baseline)[1]
    assert run_report(report(["LGPL-3.0-only"], version="2"), baseline=baseline)[1]
    installed[0]["files"][0]["sha256"] = "changed"
    assert run_report(original, installed=installed, baseline=baseline)[1]
    assert check.evaluate(original, [], POLICY, baseline, "python-other", "enforce")[1]


def test_expired_exception_and_new_version_require_review():
    policy = {
        **POLICY,
        "exceptions": [
            {
                "name": "demo",
                "version": "1",
                "scope": "python-core",
                "issue": "missing-license",
                "reason": "Owner accepted",
                "reviewer": "owner",
                "expires": "2026-10-16",
            }
        ],
    }
    assert not run_report(report(), policy=policy, today=date(2026, 10, 16))[1]
    assert run_report(report(), policy=policy, today=date(2026, 10, 17))[1]
    assert run_report(report(version="2"), policy=policy, today=date(2026, 10, 16))[1]


def test_forbidden_dependency_fails_during_report_rollout():
    policy = {**POLICY, "forbidden_packages": {"python-core": ["udapi"]}}
    with pytest.raises(ValueError, match="Forbidden package reintroduced"):
        run_report(report(["GPL-3.0-only"], name="udapi"), policy=policy, mode="report")


def test_python_inventory_reads_actual_wheel_metadata_and_license(tmp_path):
    dist = tmp_path / "lib/python3.12/site-packages/demo-1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: demo\nVersion: 1\n")
    (dist / "LICENSE").write_text("license contents")
    records = check.inventory("python", tmp_path)
    assert records[0]["name"] == "demo"
    assert len(records[0]["files"][0]["sha256"]) == 64


def test_node_inventory_includes_scoped_packages(tmp_path):
    pkg = tmp_path / "node_modules/@scope/demo"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "@scope/demo", "version": "1"}))
    assert check.inventory("node", tmp_path)[0]["name"] == "@scope/demo"


def test_bundled_license_text_is_reviewed_separately():
    data = report(["MIT"])
    data["Results"].append(
        {
            "Type": "license-file",
            "Target": "third_party/LICENSE",
            "Licenses": [{"Name": "GPL-3.0-only", "FilePath": "third_party/LICENSE"}],
        }
    )
    proof = {"third_party/LICENSE": {"path": "third_party/LICENSE", "sha256": "first"}}
    result, failed = run_report(data, bundled_evidence=proof)
    assert failed
    assert result["findings"][0]["issue"] == "bundled-license"
    baseline = {
        "reviewed": True,
        "findings": [{**result["findings"][0], "reason": "Reviewed copied license", "reviewer": "maintainer"}],
    }
    assert not run_report(data, bundled_evidence=proof, baseline=baseline)[1]
    proof["third_party/LICENSE"]["sha256"] = "changed"
    assert run_report(data, bundled_evidence=proof, baseline=baseline)[1]


@pytest.mark.parametrize(
    "name",
    [
        "uv.lock",
        "packages/example/pyproject.toml",
        "plugins/new/requirements.txt",
        "nested/package-lock.json",
        "Dockerfile.core",
        "images/custom.Dockerfile",
        "nested/.dockerignore",
        "images/custom.Dockerfile.dockerignore",
        "docker-bake.hcl",
        "compose.yaml",
        "uv.toml",
        ".python-version",
        "scripts/build.py",
        ".github/actions/license-scan/action.yml",
        ".github/licenses/policy.json",
    ],
)
def test_dependency_and_build_paths_require_image(name):
    assert ci.impacts_image(name)


@pytest.mark.parametrize("name", ["README.md", "docs/guide.md", "altk_evolve/backend/filesystem.py", "tests/unit/test_client.py"])
def test_code_and_docs_do_not_build_image(name):
    assert not ci.impacts_image(name)


def test_pr_diff_consumes_more_than_300_files():
    event = {"pull_request": {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}
    assert ci.plan("pull_request", event, lambda *_: [f"docs/{i}.md" for i in range(500)] + ["uv.lock"])
    assert not ci.plan("pull_request", event, lambda *_: ["README.md"])
    with pytest.raises(RuntimeError):
        ci.plan("pull_request", event, lambda *_: (_ for _ in ()).throw(RuntimeError("git failed")))


def test_push_and_manual_conservatively_build():
    import subprocess

    for before in [None, "0" * 40]:
        assert ci.plan("push", {"ref": "refs/heads/main", "before": before})
    event = {"ref": "refs/heads/main", "before": "a" * 40, "after": "b" * 40}
    assert ci.plan("push", event, lambda *_: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "git")))
    assert not ci.plan("push", event, lambda *_: ["docs/a.md"])
    assert ci.plan("workflow_dispatch", {})
    with pytest.raises(ValueError):
        ci.plan("push", {"ref": "refs/heads/untrusted"})


def test_real_git_range_tracks_deleted_or_renamed_manifest(tmp_path, monkeypatch):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Fixture")
    (tmp_path / "uv.lock").write_text("synthetic lock")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("mv", "uv.lock", "ordinary.txt")
    git("commit", "-qm", "rename")
    head = git("rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    assert set(ci.changed_paths(base, head)) == {"uv.lock", "ordinary.txt"}
    assert ci.plan("push", {"ref": "refs/heads/main", "before": base, "after": head})


def jobs(image="false"):
    result = {name: {"result": "success"} for name in ["plan", "policy", "python", "frontend", "image"]}
    result["plan"]["outputs"] = {"image": image}
    result["image"]["result"] = "success" if image == "true" else "skipped"
    return result


def test_aggregate_accepts_only_planned_image_skip():
    ci.aggregate(jobs())
    ci.aggregate(jobs("true"))
    for name in ["plan", "policy", "python", "frontend"]:
        for status in ["failure", "cancelled", "skipped"]:
            value = jobs()
            value[name]["result"] = status
            with pytest.raises(ValueError):
                ci.aggregate(value)
    for value in [jobs(""), jobs("unexpected"), {}]:
        with pytest.raises(ValueError):
            ci.aggregate(value)
    value = jobs("true")
    value["image"]["result"] = "skipped"
    with pytest.raises(ValueError):
        ci.aggregate(value)


def test_policy_bootstrap_cannot_enable_enforcement(tmp_path):
    base, current = tmp_path / "base", tmp_path / "current"
    current.mkdir()
    for name in ["policy.json", "baseline.json"]:
        (current / name).write_text("{}")
    assert ci.select_policy("report", base, current) == current
    with pytest.raises(ValueError, match="report mode only"):
        ci.select_policy("enforce", base, current)
    base.mkdir()
    with pytest.raises(ValueError, match="incomplete"):
        ci.select_policy("report", base, current)
    for name in ["policy.json", "baseline.json"]:
        (base / name).write_text("{}")
    assert ci.select_policy("enforce", base, current) == base


def test_npm_inventory_includes_nested_packages_without_borrowing_licenses(tmp_path):
    root = tmp_path / "node_modules"
    parent = root / "parent"
    child = parent / "node_modules/@scope/child"
    child.mkdir(parents=True)
    for path, name in [(parent, "parent"), (child, "@scope/child")]:
        (path / "package.json").write_text(json.dumps({"name": name, "version": "1"}))
    (child / "LICENSE").write_text("MIT")
    records = {p["name"]: p for p in check.inventory("node", root)}
    assert set(records) == {"parent", "@scope/child"}
    assert records["parent"]["files"] == []
    assert records["@scope/child"]["files"]


def test_profiles_include_all_workspace_packages_and_optional_extras():
    assert check.profile_arguments("core") == ["--frozen", "--no-dev", "--package", "altk-evolve-core"]
    assert check.profile_arguments("convenience")[-1] == "altk-evolve"
    assert check.profile_arguments("optional")[-2:] == ["--all-packages", "--all-extras"]


def test_empty_independent_inventory_is_rejected_by_cli(tmp_path, monkeypatch):
    import sys

    inventory = tmp_path / "inventory.json"
    inventory.write_text("[]")
    monkeypatch.setattr(
        sys, "argv", ["check.py", "evaluate", "--report", "unused", "--inventory", str(inventory), "--scope", "test", "--output", "unused"]
    )
    with pytest.raises(ValueError, match="Empty independent"):
        check.main()
