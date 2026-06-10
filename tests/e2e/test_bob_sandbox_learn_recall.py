"""End-to-end test of the evolve-lite learn + recall flow in the Bob sandbox.

Runs three sequential Bob CLI sessions against the Dockerized Bob sandbox:
  1. Session 1 performs an EXIF task, then explicitly invokes the evolve-lite
     save-trajectory and learn skills so a trajectory and guideline are saved.
  2. Session 2 asks a related EXIF question. The recall skill should surface
     the guideline from session 1 before substantive work begins.
  3. Session 3 runs the offline provenance skill so the recall audit gets
     follow-up influence verdicts.

Requires Docker, the ``evolve-bob-sandbox`` image built, and a persisted Bob
SSO auth state on the host (created by ``just bob-auth``). The test mounts
that auth state read-write into the container alongside a stable hostname so
Bob's encrypted file storage decrypts across runs.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


log = logging.getLogger(__name__)


SANDBOX_IMAGE = "evolve-bob-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_TIMEOUT_SECONDS = 600
BOB_HOSTNAME = os.environ.get("BOB_HOSTNAME", "evolve-bob-sandbox")
BOB_SSO_PORT = os.environ.get("BOB_SSO_PORT", "47687")
BOB_HOME_DEFAULT = REPO_ROOT / ".bob-sandbox-home"


@pytest.fixture(scope="session")
def bob_sandbox_ready():
    """Skip if Docker, the Bob sandbox image, or persisted auth aren't available."""
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not running")

    image_check = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
    )
    if image_check.returncode != 0:
        pytest.skip(f"sandbox image {SANDBOX_IMAGE!r} not built — run `just sandbox-build bob`")

    bob_home = Path(os.environ.get("BOB_HOME", str(BOB_HOME_DEFAULT)))
    if not bob_home.is_dir() or not (bob_home / "settings.json").is_file():
        pytest.skip(f"bob auth state missing at {bob_home} — run `just bob-auth` first")

    return bob_home


@pytest.fixture
def bob_workspace(tmp_path):
    """Copy demo/workspace and install the Bob plugin into it."""
    src = REPO_ROOT / "demo" / "workspace"
    workspace = tmp_path / "workspace"
    shutil.copytree(src, workspace, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))

    install_script = REPO_ROOT / "platform-integrations" / "install.sh"
    result = subprocess.run(
        ["bash", str(install_script), "install", "--platform", "bob", "--dir", str(workspace)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"bob install failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    return workspace


def _run_bob_prompt(
    workspace: Path,
    bob_home: Path,
    prompt: str,
) -> subprocess.CompletedProcess:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--hostname",
        BOB_HOSTNAME,
        "--env",
        "BOB_SHELL_FORCE_FILE_STORAGE=true",
        "--env",
        f"SSO_PORT={BOB_SSO_PORT}",
        "--env",
        "EVOLVE_DEBUG=1",
        "--env",
        "TMPDIR=/workspace/.evolve/tmp",
        "--publish",
        f"127.0.0.1:{BOB_SSO_PORT}:{BOB_SSO_PORT}",
        "-v",
        f"{workspace}:/workspace",
        "-v",
        f"{bob_home}:/home/sandbox/.bob",
        SANDBOX_IMAGE,
        "bob",
        "--accept-license",
        "--auth-method",
        "sso",
        "--yolo",
        "-p",
        prompt,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)


def _audit_events(evolve_dir: Path) -> list[dict]:
    audit_log = evolve_dir / "audit.log"
    if not audit_log.is_file():
        return []
    return [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]


@pytest.mark.e2e
def test_bob_learn_then_recall_flow(bob_sandbox_ready, bob_workspace):
    """Session 1 learns, session 2 recalls, session 3 records influence."""
    bob_home = bob_sandbox_ready
    evolve_dir = bob_workspace / ".evolve"

    log.info("bob session 1: running seed task with save-trajectory + learn...")
    t0 = time.time()
    result1 = _run_bob_prompt(
        bob_workspace,
        bob_home,
        (
            "Where was the photo @sample.jpg taken? Use EXIF metadata. "
            "When done, invoke the evolve-lite save-trajectory skill, then invoke the evolve-lite learn skill. "
            "Do not skip either evolve-lite skill."
        ),
    )
    log.info(f"bob session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, (
        f"session 1 exited {result1.returncode}\nstdout:\n{result1.stdout[-2000:]}\nstderr:\n{result1.stderr[-2000:]}"
    )

    trajectories_dir = evolve_dir / "trajectories"
    entities_dir = evolve_dir / "entities"
    assert trajectories_dir.is_dir(), f"{trajectories_dir} was not created"
    trajectories = list(trajectories_dir.glob("*.json")) + list(trajectories_dir.glob("*.jsonl"))
    assert trajectories, f"no Bob trajectory files found in {trajectories_dir}"
    assert entities_dir.is_dir(), f"{entities_dir} was not created"
    entity_files = list(entities_dir.rglob("*.md"))
    assert entity_files, f"no guideline files found in {entities_dir}"

    log.info("bob session 2: running related task to exercise recall...")
    t1 = time.time()
    result2 = _run_bob_prompt(
        bob_workspace,
        bob_home,
        (
            "STEP 1 (mandatory, do this first before anything else): invoke the evolve-lite recall skill "
            "to retrieve relevant stored guidelines. Do not run any other tool until recall is complete. "
            "STEP 2: answer this question using EXIF metadata, applying any guideline returned by recall: "
            "What focal length was used to take the photo @sample.jpg? "
            "STEP 3 (mandatory): invoke the evolve-lite save-trajectory skill. "
            "Do not invoke the learn skill."
        ),
    )
    log.info(f"bob session 2: exited {result2.returncode} after {time.time() - t1:.0f}s")
    assert result2.returncode == 0, (
        f"session 2 exited {result2.returncode}\nstdout:\n{result2.stdout[-2000:]}\nstderr:\n{result2.stderr[-2000:]}"
    )

    session2_trajectories = (set(trajectories_dir.glob("*.json")) | set(trajectories_dir.glob("*.jsonl"))) - set(trajectories)
    assert session2_trajectories, f"no Bob trajectory saved for session 2 in {trajectories_dir}"
    session2_trajectory = max(session2_trajectories, key=lambda p: p.stat().st_mtime)

    # Bob has no UserPromptSubmit hook, so the recall skill cannot emit a
    # recall audit event the way the codex/claude tests do. Verify recall
    # influence indirectly: session 2's saved trajectory should reference
    # one of the guideline files (or its key content) from session 1.
    learned_ids = {str(path.relative_to(entities_dir).with_suffix("")) for path in entity_files}
    session2_text = session2_trajectory.read_text(encoding="utf-8")
    assert any(eid.split("/")[-1] in session2_text for eid in learned_ids), (
        f"session 2 trajectory did not reference any guideline filename from {learned_ids}"
    )

    log.info("bob session 3: running offline provenance analysis...")
    t2 = time.time()
    session2_id = session2_trajectory.stem
    result3 = _run_bob_prompt(
        bob_workspace,
        bob_home,
        (
            "Run the evolve-lite provenance skill now. Analyze the saved trajectories in "
            ".evolve/trajectories/. Record influence verdicts for the guidelines under "
            f".evolve/entities/guideline/ as applied (or not) in trajectory {session2_id}. "
            "Do not modify source files."
        ),
    )
    log.info(f"bob session 3: exited {result3.returncode} after {time.time() - t2:.0f}s")
    assert result3.returncode == 0, (
        f"session 3 exited {result3.returncode}\nstdout:\n{result3.stdout[-2000:]}\nstderr:\n{result3.stderr[-2000:]}"
    )

    events = _audit_events(evolve_dir)
    influence_events = [event for event in events if event.get("event") == "influence"]
    assert influence_events, f"no influence audit event recorded. all events: {events}"
    influenced_ids = {event.get("entity") for event in influence_events}
    assert influenced_ids & learned_ids, f"influence events {influence_events} did not assess any learned ids {learned_ids}"
    allowed_verdicts = {"followed", "contradicted", "not_applicable"}
    assert any(event.get("verdict") in allowed_verdicts for event in influence_events), (
        f"no learned guideline was assessed with an allowed verdict. influence events: {influence_events}"
    )
    for event in influence_events:
        assert event.get("verdict") in allowed_verdicts
        assert event.get("evidence"), f"influence event missing evidence: {event}"
