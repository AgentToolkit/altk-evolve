"""Unit tests for the helpers in tests/e2e/test_codex_sandbox_learn_recall.py.

The e2e test needs Docker, an image and credentials, so it rarely runs. These
pin down the parts of its logic that can be checked without any of them.
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

_E2E_MODULE_PATH = Path(__file__).resolve().parents[1] / "e2e" / "test_codex_sandbox_learn_recall.py"
_PROVIDER_ENV_VARS = (
    "CODEX_MODEL",
    "CODEX_MODEL_PROVIDER",
    "CODEX_MODEL_PROVIDER_BASE_URL",
    "CODEX_MODEL_PROVIDER_NAME",
    "CODEX_MODEL_PROVIDER_ENV_KEY",
    "CODEX_MODEL_PROVIDER_WIRE_API",
    "OPENAI_BASE_URL",
)


@pytest.fixture(scope="module")
def e2e():
    spec = importlib.util.spec_from_file_location("_codex_sandbox_e2e_under_test", _E2E_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_provider_env(monkeypatch):
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _write_audit_log(evolve_dir: Path, rows: list[dict]) -> None:
    evolve_dir.mkdir(parents=True, exist_ok=True)
    (evolve_dir / "audit.log").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class TestProviderConfig:
    def test_provider_without_base_url_fails_before_config_is_written(self, e2e, clean_provider_env):
        # Codex refuses to load such a config: `codex plugin marketplace add`
        # exits with "Model provider `gw` not found". The workspace fixture
        # writes the config, so it has to fail there with the real cause.
        clean_provider_env.setenv("CODEX_MODEL_PROVIDER", "gw")
        with pytest.raises(pytest.fail.Exception, match="CODEX_MODEL_PROVIDER='gw' is set but neither"):
            e2e._codex_config_lines()

    def test_base_url_without_provider_fails(self, e2e, clean_provider_env):
        clean_provider_env.setenv("CODEX_MODEL_PROVIDER_BASE_URL", "https://gateway.example/v1")
        with pytest.raises(pytest.fail.Exception, match="CODEX_MODEL_PROVIDER_BASE_URL is set but CODEX_MODEL_PROVIDER is not"):
            e2e._codex_config_lines()

    def test_provider_with_base_url_writes_provider_table(self, e2e, clean_provider_env):
        clean_provider_env.setenv("CODEX_MODEL_PROVIDER", "gw")
        clean_provider_env.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
        lines = e2e._codex_config_lines()
        assert 'model_provider = "gw"' in lines
        assert '[model_providers."gw"]' in lines
        assert 'base_url = "https://gateway.example/v1"' in lines

    def test_no_provider_env_writes_empty_config(self, e2e, clean_provider_env):
        assert e2e._codex_config_lines() == []


class TestRecallScoping:
    def test_recall_rows_written_before_session_2_are_ignored(self, e2e, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        # Session 1 logs a recall of the entity it just saved; session 2 logs
        # nothing. Session 2 skipped recall, so there must be no match.
        _write_audit_log(evolve_dir, [{"event": "recall", "session_id": "s1", "entities": ["reference/exif-no-tools"]}])
        rows_before_session2 = len(e2e._audit_events(evolve_dir))

        assert e2e._recall_events(evolve_dir, since=rows_before_session2) == []

    def test_recall_rows_written_by_session_2_are_returned(self, e2e, tmp_path):
        evolve_dir = tmp_path / ".evolve"
        session1 = {"event": "recall", "session_id": "s1", "entities": ["reference/exif-no-tools"]}
        _write_audit_log(evolve_dir, [session1])
        rows_before_session2 = len(e2e._audit_events(evolve_dir))
        session2 = {"event": "recall", "session_id": "s2", "entities": ["reference/exif-no-tools"]}
        influence = {"event": "influence", "session_id": "s2", "entity": "reference/exif-no-tools"}
        _write_audit_log(evolve_dir, [session1, session2, influence])

        assert e2e._recall_events(evolve_dir, since=rows_before_session2) == [session2]


class TestTrajectorySessionIds:
    def test_reads_session_id_from_each_trajectory(self, e2e, tmp_path):
        with_id = tmp_path / "trajectory_2026-10-15T10-00-00_019a-thread.json"
        with_id.write_text(json.dumps({"session_id": "019a-thread", "messages": []}), encoding="utf-8")
        without_id = tmp_path / "trajectory_2026-10-15T10-00-01.json"
        without_id.write_text(json.dumps({"messages": []}), encoding="utf-8")

        assert e2e._trajectory_session_ids([with_id, without_id]) == {"019a-thread"}
        assert e2e._trajectory_session_ids([without_id]) == set()


class TestContainerTimeout:
    def test_timed_out_container_is_removed(self, e2e, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(e2e.subprocess, "run", fake_run)

        with pytest.raises(subprocess.TimeoutExpired):
            e2e._run_in_container(tmp_path, ["codex", "exec", "hi"], timeout=5)

        run_cmd, rm_cmd = calls
        name = run_cmd[run_cmd.index("--name") + 1]
        assert name.startswith(e2e.CONTAINER_NAME_PREFIX)
        assert rm_cmd == ["docker", "rm", "--force", name]

    def test_interrupted_container_is_removed(self, e2e, tmp_path, monkeypatch):
        # Ctrl-C during a session: subprocess kills only the docker client, so
        # the container (and the Codex session in it) must be removed too.
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["docker", "run"]:
                raise KeyboardInterrupt
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(e2e.subprocess, "run", fake_run)

        with pytest.raises(KeyboardInterrupt):
            e2e._run_in_container(tmp_path, ["codex", "exec", "hi"], timeout=5)

        run_cmd, rm_cmd = calls
        assert rm_cmd == ["docker", "rm", "--force", run_cmd[run_cmd.index("--name") + 1]]

    def test_failed_cleanup_does_not_mask_the_original_exception(self, e2e, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["docker", "run"]:
                raise KeyboardInterrupt
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

        monkeypatch.setattr(e2e.subprocess, "run", fake_run)

        with pytest.raises(KeyboardInterrupt):
            e2e._run_in_container(tmp_path, ["codex", "exec", "hi"], timeout=5)
        assert calls[1][:3] == ["docker", "rm", "--force"]

    def test_successful_run_does_not_remove_the_container(self, e2e, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr(e2e.subprocess, "run", fake_run)

        assert e2e._run_in_container(tmp_path, ["false"], timeout=5).returncode == 1
        assert [cmd[:2] for cmd in calls] == [["docker", "run"]]

    def test_each_run_gets_its_own_container_name(self, e2e, tmp_path, monkeypatch):
        names: list[str] = []

        def fake_run(cmd, **kwargs):
            names.append(cmd[cmd.index("--name") + 1])
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(e2e.subprocess, "run", fake_run)
        e2e._run_in_container(tmp_path, ["true"], timeout=5)
        e2e._run_in_container(tmp_path, ["true"], timeout=5)

        assert len(set(names)) == 2
