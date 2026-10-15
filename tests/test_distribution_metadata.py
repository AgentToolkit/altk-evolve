"""Packaging contracts: core never depends on convenience; releases stay paired."""

from pathlib import Path
import tomllib
import runpy

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
render = runpy.run_path(str(ROOT / "scripts/sync_convenience.py"))["render"]


def test_core_and_convenience_dependency_boundaries():
    core = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    convenience_file = ROOT / "packages/altk-evolve/pyproject.toml"
    assert convenience_file.read_text() == render(core)
    convenience = tomllib.loads(convenience_file.read_text())["project"]
    assert core["name"] == "altk-evolve-core"
    forbidden = {
        "arize-phoenix",
        "fastembed",
        "sentence-transformers",
        "torch",
        "readi-privacy",
        "pandas",
        "scipy",
        "fastapi",
        "fastmcp",
        "uvicorn",
        "typer",
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-http",
    }
    assert not forbidden.intersection(Requirement(r).name for r in core["dependencies"])
    for dependencies in [core["dependencies"], *core["optional-dependencies"].values()]:
        assert all(Requirement(r).name != "altk-evolve" for r in dependencies)
    assert set(convenience["optional-dependencies"]) == set(core["optional-dependencies"])
    default = Requirement(convenience["dependencies"][0])
    assert default.name == "altk-evolve-core"
    assert default.extras == {"server", "cli", "fastembed", "tracing"}
    assert str(default.specifier) == f"=={core['version']}"
    assert "scripts" not in convenience


def test_version_bump_updates_all_forwarded_dependencies():
    core = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    core["version"] = "9.8.7"
    generated = tomllib.loads(render(core))["project"]
    assert generated["version"] == "9.8.7"
    for dependencies in [generated["dependencies"], *generated["optional-dependencies"].values()]:
        assert all(str(Requirement(r).specifier) == "==9.8.7" for r in dependencies)
