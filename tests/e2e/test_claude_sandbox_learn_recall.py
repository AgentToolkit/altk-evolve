"""End-to-end test of the evolve-lite learn + recall flow in the sandbox.

Runs two sequential Claude Code sessions against the Dockerized sandbox:
  1. Ask about photo location — sandbox lacks exiftool/PIL, so Claude hits
     dead ends and recovers. Stop hook fires learn, which reads the saved
     transcript and extracts a guideline.
  2. Ask about focal length — UserPromptSubmit recall hook injects the
     guideline from session 1, so Claude should skip the dead ends.
  3. Run the offline provenance skill to record whether the recalled
     guideline influenced session 2.

Assertions:
  - Session 1 produces a guideline file under .evolve/entities/.
  - Session 2 does NOT invoke exiftool/PIL (recall shortcut worked).

Requires Docker, the `claude-sandbox` image built, and ANTHROPIC_API_KEY
set in the environment (forwarded into the container).
"""

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest


log = logging.getLogger(__name__)


SANDBOX_IMAGE = "claude-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_TIMEOUT_SECONDS = 600
FORWARDED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_MODEL",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
)


@pytest.fixture(scope="session")
def sandbox_ready():
    """Skip if Docker, the sandbox image, or credentials aren't available."""
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not running")

    image_check = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
    )
    if image_check.returncode != 0:
        pytest.skip(f"sandbox image {SANDBOX_IMAGE!r} not built — run `just sandbox-build claude`")

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        pytest.skip("ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) not set in environment")

    return True


@pytest.fixture
def sandbox_workspace(tmp_path):
    """Copy demo/workspace to tmp_path so each test gets a clean state."""
    src = REPO_ROOT / "demo" / "workspace"
    dst = tmp_path / "workspace"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))
    return dst


def _run_sandbox_prompt(workspace: Path, prompt: str) -> subprocess.CompletedProcess:
    plugins = REPO_ROOT / "platform-integrations" / "claude" / "plugins"
    command = "claude --plugin-dir /plugins/evolve-lite/ --dangerously-skip-permissions -p " + shlex.quote(prompt)
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e",
        "EVOLVE_DEBUG=1",
        "-v",
        f"{workspace}:/workspace",
        "-v",
        f"{plugins}:/plugins",
        SANDBOX_IMAGE,
        "bash",
        "-c",
        command,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)


def _bash_commands(transcript_path: Path) -> list[str]:
    commands = []
    for line in transcript_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = record.get("message", {}).get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = block.get("input", {}).get("command", "")
                if cmd:
                    commands.append(cmd)
    return commands


@pytest.mark.e2e
def test_claude_learn_then_recall_flow(sandbox_ready, sandbox_workspace):
    """Session 1 learns, session 2 recalls, session 3 records influence."""
    del sandbox_ready  # only used for its skip side effect

    # --- Session 1: location query — expected dead ends then recovery ---
    log.info("session 1: running location query in sandbox...")
    t0 = time.time()
    result1 = _run_sandbox_prompt(
        sandbox_workspace,
        "where was the photo @sample.jpg taken. use exif metadata",
    )
    log.info(f"session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, f"session 1 exited {result1.returncode}\nstderr:\n{result1.stderr[-2000:]}"

    entities_dir = sandbox_workspace / ".evolve" / "entities"
    trajectories_dir = sandbox_workspace / ".evolve" / "trajectories"

    assert entities_dir.is_dir(), f"{entities_dir} was not created — learn did not save guidelines.\nstdout:\n{result1.stdout[-2000:]}"
    entity_files = list(entities_dir.rglob("*.md"))
    assert entity_files, f"no guideline files found in {entities_dir}"
    log.info(f"session 1: learn saved {len(entity_files)} guideline(s): {[p.name for p in entity_files]}")

    transcripts = list(trajectories_dir.glob("*.jsonl"))
    assert transcripts, f"no transcript saved in {trajectories_dir}"

    # --- Session 2: focal length query — recall should inject the guideline ---
    log.info("session 2: running focal length query in sandbox...")
    t1 = time.time()
    result2 = _run_sandbox_prompt(
        sandbox_workspace,
        "what focal length was used to take the photo @sample.jpg. use exif metadata",
    )
    log.info(f"session 2: exited {result2.returncode} after {time.time() - t1:.0f}s")
    assert result2.returncode == 0, f"session 2 exited {result2.returncode}\nstderr:\n{result2.stderr[-2000:]}"

    session2_transcripts = [p for p in trajectories_dir.glob("*.jsonl") if p not in transcripts]
    assert session2_transcripts, "no new transcript saved for session 2"
    session2_transcript = max(session2_transcripts, key=lambda p: p.stat().st_mtime)

    commands = _bash_commands(session2_transcript)
    log.info(f"session 2: checking {len(commands)} bash commands for forbidden tools")
    joined = "\n".join(commands).lower()

    # Recall should steer Claude away from tools guaranteed-unavailable in the
    # sandbox. Only `exiftool` is definitively absent (not installed, can't be
    # pip-installed). Other libraries (PIL, piexif, exifread) may appear in a
    # valid guideline as "install via pip and use", so we don't ban them.
    assert not re.search(r"\bexiftool\b", joined), "session 2 invoked exiftool despite recall guideline:\n" + "\n".join(commands)

    # --- Usage provenance: audit.log should record recall ---
    audit_log = sandbox_workspace / ".evolve" / "audit.log"
    assert audit_log.is_file(), f"{audit_log} was not created — recall did not append audit events"

    events = []
    for line in audit_log.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

    session2_id = session2_transcript.stem.removeprefix("claude-transcript_")
    session1_ids = {str(path.relative_to(entities_dir).with_suffix("")) for path in entity_files}

    recall_events = [event for event in events if event.get("event") == "recall" and event.get("session_id") == session2_id]
    assert recall_events, f"no recall audit event for session 2 ({session2_id}). all events: {events}"
    recalled_ids = {entity_id for event in recall_events for entity_id in event.get("entities", [])}
    assert recalled_ids & session1_ids, f"recall event entities {recalled_ids} did not include any id from session 1 ({session1_ids})"
    log.info(f"session 2: audit recorded recall of {recalled_ids}")

    # --- Offline provenance: audit.log should record usefulness verdicts ---
    log.info("session 3: running offline provenance analysis...")
    t2 = time.time()
    result3 = _run_sandbox_prompt(
        sandbox_workspace,
        (
            "Run /evolve-lite:provenance now. Analyze the saved trajectories and "
            "the recall events in .evolve/audit.log. Record influence verdicts "
            "for any recalled guideline that can be matched to the focal-length "
            "photo session. Do not modify source files."
        ),
    )
    log.info(f"session 3: exited {result3.returncode} after {time.time() - t2:.0f}s")
    assert result3.returncode == 0, f"session 3 exited {result3.returncode}\nstderr:\n{result3.stderr[-2000:]}"

    events = []
    for line in audit_log.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

    influence_events = [event for event in events if event.get("event") == "influence"]
    assert influence_events, f"no influence audit event recorded. all events: {events}"
    influenced_ids = {event.get("entity") for event in influence_events}
    assert influenced_ids & recalled_ids, f"influence events {influence_events} did not assess any recalled ids {recalled_ids}"
    for event in influence_events:
        assert event.get("verdict") in {"followed", "contradicted", "not_applicable"}
        assert event.get("evidence"), f"influence event missing evidence: {event}"
