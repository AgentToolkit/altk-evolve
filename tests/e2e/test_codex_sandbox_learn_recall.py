"""End-to-end test of the evolve-lite learn + recall flow in the Codex sandbox.

Runs two sequential Codex sessions against the Dockerized Codex sandbox:
  1. Session 1 performs an EXIF task, then explicitly runs save-trajectory
     and learn so a trajectory and guideline are saved.
  2. Session 2 asks a related EXIF question. The Codex UserPromptSubmit hook
     should inject recalled guidance before the prompt is handled.
  3. Session 3 runs the offline provenance skill so the recall audit gets
     follow-up influence verdicts.

Requires Docker, the `evolve-codex-sandbox` image built, and Codex credentials
exported in the environment.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable

import pytest


log = logging.getLogger(__name__)


SANDBOX_IMAGE = "evolve-codex-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_TIMEOUT_SECONDS = 600
FORWARDED_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "CODEX_MODEL",
)
CODEX_PROVIDER_ENV_KEY_VAR = "CODEX_MODEL_PROVIDER_ENV_KEY"


@pytest.fixture(scope="session")
def codex_sandbox_ready():
    """Skip if Docker, the Codex sandbox image, or credentials aren't available."""
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not running")

    image_check = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
    )
    if image_check.returncode != 0:
        pytest.skip(f"sandbox image {SANDBOX_IMAGE!r} not built - run `just sandbox-build codex`")

    credential_env_var = os.environ.get(CODEX_PROVIDER_ENV_KEY_VAR, "OPENAI_API_KEY")
    if not os.environ.get(credential_env_var):
        pytest.skip(f"{credential_env_var} not set in environment")

    return True


@pytest.fixture
def codex_workspace(tmp_path):
    """Copy demo/workspace and install the Codex plugin into it."""
    src = REPO_ROOT / "demo" / "workspace"
    workspace = tmp_path / "workspace"
    shutil.copytree(src, workspace, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))

    install_script = REPO_ROOT / "platform-integrations" / "install.sh"
    result = subprocess.run(
        ["bash", str(install_script), "install", "--platform", "codex", "--dir", str(workspace)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"codex install failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    _register_codex_plugin_for_container(workspace)
    return workspace


def _toml_str(value: str) -> str:
    return json.dumps(value)


def _codex_config_lines() -> list[str]:
    lines: list[str] = []
    if model := os.environ.get("CODEX_MODEL"):
        lines.append(f"model = {_toml_str(model)}")

    provider = os.environ.get("CODEX_MODEL_PROVIDER")
    if provider:
        lines.append(f"model_provider = {_toml_str(provider)}")

    base_url = os.environ.get("CODEX_MODEL_PROVIDER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    if provider and base_url:
        provider_name = os.environ.get("CODEX_MODEL_PROVIDER_NAME", provider)
        provider_env_key = os.environ.get(CODEX_PROVIDER_ENV_KEY_VAR, "OPENAI_API_KEY")
        lines.extend(
            [
                "",
                f"[model_providers.{_toml_str(provider)}]",
                f"name = {_toml_str(provider_name)}",
                f"base_url = {_toml_str(base_url)}",
                f"env_key = {_toml_str(provider_env_key)}",
            ]
        )
        if wire_api := os.environ.get("CODEX_MODEL_PROVIDER_WIRE_API"):
            lines.append(f"wire_api = {_toml_str(wire_api)}")

    if lines:
        lines.append("")
    return lines


def _register_codex_plugin_for_container(workspace: Path) -> None:
    """Pre-populate /codex-home with a local marketplace plugin cache.

    This mirrors the headless registration used by tests/smoke_skills.py, but
    writes paths as the container sees them: workspace is mounted at /workspace
    and CODEX_HOME is mounted at /codex-home.
    """
    codex_home = workspace / ".codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)

    plugin_src = workspace / "plugins" / "evolve-lite"
    plugin_json = plugin_src / ".codex-plugin" / "plugin.json"
    version = json.loads(plugin_json.read_text(encoding="utf-8")).get("version", "0.0.0")
    cache_dir = codex_home / "plugins" / "cache" / "evolve-local" / "evolve-lite" / version
    cache_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(plugin_src / ".codex-plugin", cache_dir / ".codex-plugin", dirs_exist_ok=True)
    shutil.copytree(plugin_src / "lib", cache_dir / "lib", dirs_exist_ok=True)
    shutil.copytree(plugin_src / "skills" / "evolve-lite", cache_dir / "skills", dirs_exist_ok=True)

    config = "\n".join(_codex_config_lines())
    config += """[marketplaces.evolve-local]
source = "/workspace"

[plugins."evolve-lite@evolve-local"]
enabled = true
"""
    (codex_home / "config.toml").write_text(config, encoding="utf-8")


def _forwarded_env_vars() -> Iterable[str]:
    yield from FORWARDED_ENV_VARS
    provider_env_key = os.environ.get(CODEX_PROVIDER_ENV_KEY_VAR)
    if provider_env_key:
        yield provider_env_key


def _run_codex_prompt(workspace: Path, prompt: str, *, enable_hooks: bool = True) -> subprocess.CompletedProcess:
    codex_home = workspace / ".codex-home"
    cmd = ["docker", "run", "--rm"]
    for var in _forwarded_env_vars():
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e",
        "EVOLVE_DEBUG=1",
        "-e",
        "TMPDIR=/workspace/.evolve/tmp",
        "-v",
        f"{workspace}:/workspace",
        "-v",
        f"{codex_home}:/codex-home",
        SANDBOX_IMAGE,
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "-c",
        f"features.codex_hooks={str(enable_hooks).lower()}",
        "-C",
        "/workspace",
        prompt,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)


def _audit_events(evolve_dir: Path) -> list[dict]:
    audit_log = evolve_dir / "audit.log"
    if not audit_log.is_file():
        return []
    return [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]


@pytest.mark.e2e
def test_codex_learn_then_recall_flow(codex_sandbox_ready, codex_workspace):
    """Session 1 learns, session 2 recalls, session 3 records influence."""
    del codex_sandbox_ready

    evolve_dir = codex_workspace / ".evolve"

    log.info("codex session 1: running seed task with save-trajectory + learn...")
    t0 = time.time()
    result1 = _run_codex_prompt(
        codex_workspace,
        (
            "Where was the photo @sample.jpg taken? Use EXIF metadata. "
            "When done, invoke the evolve-lite save-trajectory skill, then invoke the evolve-lite learn skill. "
            "Do not skip either evolve-lite skill."
        ),
    )
    log.info(f"codex session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, (
        f"session 1 exited {result1.returncode}\nstdout:\n{result1.stdout[-2000:]}\nstderr:\n{result1.stderr[-2000:]}"
    )

    trajectories_dir = evolve_dir / "trajectories"
    entities_dir = evolve_dir / "entities"
    assert trajectories_dir.is_dir(), f"{trajectories_dir} was not created"
    trajectories = list(trajectories_dir.glob("*.json"))
    assert trajectories, f"no Codex trajectory JSON files found in {trajectories_dir}"
    assert entities_dir.is_dir(), f"{entities_dir} was not created"
    entity_files = list(entities_dir.rglob("*.md"))
    assert entity_files, f"no guideline files found in {entities_dir}"

    log.info("codex session 2: running related task to exercise recall hook...")
    t1 = time.time()
    result2 = _run_codex_prompt(
        codex_workspace,
        (
            "What focal length was used to take the photo @sample.jpg? Use EXIF metadata. "
            "When done, invoke the evolve-lite save-trajectory skill. Do not invoke the learn skill."
        ),
    )
    log.info(f"codex session 2: exited {result2.returncode} after {time.time() - t1:.0f}s")
    assert result2.returncode == 0, (
        f"session 2 exited {result2.returncode}\nstdout:\n{result2.stdout[-2000:]}\nstderr:\n{result2.stderr[-2000:]}"
    )

    session2_trajectories = {path for path in trajectories_dir.glob("*.json")} - set(trajectories)
    assert session2_trajectories, f"no Codex trajectory saved for session 2 in {trajectories_dir}"

    events = _audit_events(evolve_dir)
    recall_events = [event for event in events if event.get("event") == "recall"]
    assert recall_events, f"no recall audit event recorded. all events: {events}"
    task_recall_event = recall_events[-1]
    task_session_id = task_recall_event["session_id"]
    recalled_ids = {entity_id for event in recall_events for entity_id in event.get("entities", [])}
    task_recalled_ids = set(task_recall_event.get("entities", []))
    learned_ids = {str(path.relative_to(entities_dir).with_suffix("")) for path in entity_files}
    assert recalled_ids & learned_ids, f"recalled ids {recalled_ids} did not include learned ids {learned_ids}"

    log.info("codex session 3: running offline provenance analysis...")
    t2 = time.time()
    result3 = _run_codex_prompt(
        codex_workspace,
        (
            "Run the evolve-lite provenance skill now. Analyze the saved trajectories and "
            "the recall events in .evolve/audit.log. Record influence verdicts "
            f"for recalled guidelines in session {task_session_id}, the focal-length "
            "photo session. Do not modify source files."
        ),
        enable_hooks=False,
    )
    log.info(f"codex session 3: exited {result3.returncode} after {time.time() - t2:.0f}s")
    assert result3.returncode == 0, (
        f"session 3 exited {result3.returncode}\nstdout:\n{result3.stdout[-2000:]}\nstderr:\n{result3.stderr[-2000:]}"
    )

    events = _audit_events(evolve_dir)
    influence_events = [event for event in events if event.get("event") == "influence" and event.get("session_id") == task_session_id]
    assert influence_events, f"no influence audit event recorded. all events: {events}"
    influenced_ids = {event.get("entity") for event in influence_events}
    assert influenced_ids & task_recalled_ids, f"influence events {influence_events} did not assess task recall ids {task_recalled_ids}"
    assert any(event.get("verdict") == "followed" for event in influence_events), (
        f"no recalled guideline was followed. influence events: {influence_events}"
    )
    for event in influence_events:
        assert event.get("verdict") in {"followed", "contradicted", "not_applicable"}
        assert event.get("evidence"), f"influence event missing evidence: {event}"
