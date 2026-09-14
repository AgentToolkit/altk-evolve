"""Real CLI subprocesses discover an installed-distribution entry point and persist profiles."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def phoenix_server():
    """This processing test requires no Phoenix process."""
    yield


def test_discovered_processor_and_revisioned_cli(tmp_path):
    # A local distribution fixture uses the standard installed metadata layout.
    package = tmp_path / "installed"
    package.mkdir()
    (package / "example_processor.py").write_text("""
from pydantic import BaseModel, ConfigDict
from altk_evolve.processing import ProcessorResult
from altk_evolve.schema.core import Entity
class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
class Processor:
    id = "example.review"
    api_version = 1
    version = "1.0"
    config_model = Settings
    def __init__(self, config):
        self.config = config
    @classmethod
    def from_config(cls, config):
        return cls(config)
    def process(self, trajectory, *, context):
        return ProcessorResult(entities=[Entity(type="review", content=self.config.label)])
""")
    info = package / "example_processor-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: example-processor\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[altk_evolve.processors]\nexample.review = example_processor:Processor\n")
    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(root), str(package), os.environ.get("PYTHONPATH", "")])),
        "EVOLVE_BACKEND": "filesystem",
        "EVOLVE_DATA_DIR": str(tmp_path / "entities"),
        "EVOLVE_SQLITE_PATH": str(tmp_path / "entities.sqlite.db"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "EVOLVE_HOOKS_CONFIG": "",
    }

    def run(*args, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "altk_evolve.cli.cli", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result.stdout

    inventory = json.loads(run("processors", "list"))
    assert "example.review" in [item["id"] for item in inventory]
    run("namespaces", "create", "memories")
    profile = tmp_path / "profile.json"

    def apply(label, expected, success=True):
        profile.write_text(json.dumps({"processors": [{"id": "review", "plugin": "example.review", "config": {"label": label}}]}))
        return run("processing-profiles", "apply", "review", "--file", str(profile), "--expected-revision", str(expected), success=success)

    assert json.loads(apply("first", 0))["revision"] == 1
    assert json.loads(apply("second", 1))["revision"] == 2
    apply("stale", 1, success=False)
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"messages": []}')
    args = ("processing", "run", "--file", str(trajectory), "--namespace", "memories", "--processing-profile", "review")
    assert json.loads(run(*args))["entities"][0]["content"] == "second"
    pinned = json.loads(run(*args, "--revision", "1"))
    assert pinned["entities"][0]["content"] == "first"
    stored = json.loads((tmp_path / "entities" / "memories.json").read_text())
    assert {e["metadata"]["processing"]["revision"] for e in stored["entities"]} == {1, 2}
