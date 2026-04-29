"""End-to-end test of the evolve-lite learn + recall flow in the sandbox.

Runs two sequential Claude Code sessions against the Dockerized sandbox:
  1. Ask about photo location — sandbox lacks exiftool/PIL, so Claude hits
     dead ends and recovers. Stop hook fires learn, which reads the saved
     transcript and extracts a guideline.
  2. Ask about focal length — UserPromptSubmit recall hook injects the
     guideline from session 1, so Claude should skip the dead ends.

Assertions:
  - Session 1 produces a guideline file under .evolve/entities/.
  - Session 2 does NOT invoke exiftool/PIL (recall shortcut worked).

Requires Docker, the `claude-sandbox` image built, and ANTHROPIC_API_KEY
set in the environment (forwarded into the container).
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
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e", "EVOLVE_DEBUG=1",
        "-v", f"{workspace}:/workspace",
        "-v", f"{plugins}:/plugins",
        SANDBOX_IMAGE,
        "bash", "-c",
        f'claude --plugin-dir /plugins/evolve-lite/ --dangerously-skip-permissions -p "{prompt}"',
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
def test_learn_then_recall_flow(sandbox_ready, sandbox_workspace):
    """Session 1 extracts a guideline; session 2 benefits from recall."""
    del sandbox_ready  # only used for its skip side effect

    # --- Session 1: location query — expected dead ends then recovery ---
    log.info("session 1: running location query in sandbox...")
    t0 = time.time()
    result1 = _run_sandbox_prompt(
        sandbox_workspace,
        "where was the photo @sample.jpg taken. use exif metadata",
    )
    log.info(f"session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, (
        f"session 1 exited {result1.returncode}\nstderr:\n{result1.stderr[-2000:]}"
    )

    entities_dir = sandbox_workspace / ".evolve" / "entities"
    trajectories_dir = sandbox_workspace / ".evolve" / "trajectories"

    assert entities_dir.is_dir(), (
        f"{entities_dir} was not created — learn did not save guidelines.\n"
        f"stdout:\n{result1.stdout[-2000:]}"
    )
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
    assert result2.returncode == 0, (
        f"session 2 exited {result2.returncode}\nstderr:\n{result2.stderr[-2000:]}"
    )

    session2_transcripts = [
        p for p in trajectories_dir.glob("*.jsonl")
        if p not in transcripts
    ]
    assert session2_transcripts, "no new transcript saved for session 2"
    session2_transcript = session2_transcripts[0]

    commands = _bash_commands(session2_transcript)
    log.info(f"session 2: checking {len(commands)} bash commands for forbidden tools")
    joined = "\n".join(commands).lower()

    # Recall should steer Claude away from the tools that failed in session 1.
    # The guideline text itself may name these tools, but we're checking actual
    # bash invocations, not string mentions — so a command like
    # `python3 -c "import PIL"` would fail this check, while the guideline's
    # prose mentioning PIL as unavailable does not.
    assert "exiftool " not in joined and "exiftool$" not in joined, (
        f"session 2 invoked exiftool despite recall guideline:\n"
        + "\n".join(commands)
    )
    for banned in ("from pil", "import pil", "import piexif", "import exifread"):
        assert banned not in joined, (
            f"session 2 tried {banned!r} despite recall guideline:\n"
            + "\n".join(commands)
        )
