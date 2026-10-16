"""End-to-end test of the evolve-lite save + recall loop in the Claude sandbox.

The Claude plugin installs no hooks and ships no ``learn`` or ``recall`` skill.
Recall and save are Claude's own native memory (``~/.claude/projects/<slug>/memory/``).
``install.sh`` adds the two things native memory lacks through one instruction
file: it copies ``EVOLVE.md`` to ``<repo>/.evolve/EVOLVE.md`` and imports it from
the repo's ``CLAUDE.md``. EVOLVE.md asks Claude to:

  * after saving a native memory, invoke ``/evolve-lite:adapt-memory``, which
    mirrors it to ``.evolve/entities/<type>/<name>.md`` (``source: native-memory``);
  * after consulting native memories, run
    ``python3 ~/.claude/evolve-lite/audit_recall.py <type>/<name> ...``, which
    appends a ``recall`` event to ``.evolve/audit.log``.

``test_claude_sandbox_installs_evolve_lite`` checks that wiring inside the image
without credentials or a model call. ``test_claude_save_then_recall_flow`` runs
three sequential Claude sessions against the Dockerized sandbox:

  1. Ask where ``sample.jpg`` was taken (the sandbox has neither exiftool nor
     Pillow) and to remember how to read EXIF here. Claude saves a native memory
     and mirrors it into ``.evolve/entities/``.
  2. Ask a related EXIF question. Claude's native memory brings back the
     session-1 memory, and Claude records it with audit_recall.py.
  3. Run the offline provenance skill to record whether the recalled memory
     influenced session 2.

Installation runs inside the image, the way a user installs from a clone: a
per-test copy of the parts of this checkout the installer needs is mounted
read-only at ``/repo`` (the marketplace the plugin is read from), and the
container's ``~/.claude`` is a per-test directory. The checkout itself is never
mounted, so its gitignored files (``.env``), ``.git`` and ``.venv`` stay out of
the unrestricted agent sessions. Nothing touches your own ``~/.claude``, and the
plugin registration, native memories and transcripts persist across the
separate ``docker run`` sessions.

Whether session 2 actually avoids exiftool is logged but not asserted: the
memory's content is model-authored, so a valid memory ("pip install piexif and
read the EXIF tags") does not imply the agent stops reaching for exiftool. The
audit events cover the part evolve controls.

Requires Docker and the ``claude-sandbox`` image built (``just sandbox-build
claude``). The three-session test also needs ANTHROPIC_API_KEY (or
ANTHROPIC_AUTH_TOKEN) set in the environment (forwarded into the container).
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest


log = logging.getLogger(__name__)


SANDBOX_IMAGE = "claude-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_TIMEOUT_SECONDS = 600
PREFLIGHT_TIMEOUT_SECONDS = 180
FORWARDED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_MODEL",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
)

# Where the image's ``sandbox`` user keeps Claude's state (HOME=/home/sandbox).
CONTAINER_CLAUDE_DIR = "/home/sandbox/.claude"
# Claude names a project's transcript and memory dir after its absolute path with
# every non-alphanumeric character replaced by ``-``. The workspace is /workspace.
CONTAINER_PROJECT_SLUG = "-workspace"
# The import line install.sh puts in CLAUDE.md (CLAUDE_IMPORT_LINE).
CLAUDE_IMPORT_LINE = "@.evolve/EVOLVE.md"
# Native memory types. adapt-memory mirrors each into .evolve/entities/<type>/.
NATIVE_MEMORY_TYPES = {"user", "feedback", "project", "reference"}
# All that install.sh needs from a clone to install the Claude plugin: the
# marketplace manifest and platform-integrations/ (the installer, the rendered
# plugin, EVOLVE.md and the scripts it names). Only these are mounted at /repo.
INSTALL_SOURCE_PATHS = (".claude-plugin", "platform-integrations")


@pytest.fixture(scope="session")
def sandbox_image():
    """Skip if Docker or the sandbox image isn't available."""
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

    return SANDBOX_IMAGE


@pytest.fixture(scope="session")
def sandbox_ready(sandbox_image):
    """Additionally skip if no Anthropic credentials are set."""
    del sandbox_image
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        pytest.skip("ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) not set in environment")
    return True


@pytest.fixture
def sandbox_workspace(tmp_path, sandbox_image):
    """Copy demo/workspace to tmp_path and install evolve-lite into it, inside the image."""
    del sandbox_image
    src = REPO_ROOT / "demo" / "workspace"
    workspace = tmp_path / "workspace"
    shutil.copytree(src, workspace, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))
    _claude_home(workspace).mkdir()
    _copy_install_source(_install_source(workspace))

    _install_evolve_lite(workspace)
    return workspace


def _claude_home(workspace: Path) -> Path:
    """Host directory mounted as the container's ``~/.claude``."""
    return workspace.parent / "claude-home"


def _install_source(workspace: Path) -> Path:
    """Host directory mounted read-only as the container's ``/repo``."""
    return workspace.parent / "repo"


def _copy_install_source(dest: Path, repo_root: Path = REPO_ROOT) -> None:
    """Copy the files under ``INSTALL_SOURCE_PATHS`` from the checkout's working tree to ``dest``.

    Mounting the checkout itself would hand the ``--dangerously-skip-permissions``
    sessions, which have network access, every gitignored file in it (a ``.env``
    with API keys, ``.venv``) plus ``.git``. Only tracked and untracked,
    not-ignored files are copied, so uncommitted edits to the plugin are still
    what the test installs, and ignored files never are.
    """
    listed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *INSTALL_SOURCE_PATHS],
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, f"git ls-files failed in {repo_root}:\n{listed.stderr.decode(errors='replace')}"
    copied = 0
    for rel in filter(None, listed.stdout.decode().split("\0")):
        src = repo_root / rel
        if src.is_symlink() or not src.is_file():
            # Deleted in the working tree, or a link that could point outside it.
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    assert copied, f"nothing under {INSTALL_SOURCE_PATHS} in {repo_root}"


def _docker_run_prefix(workspace: Path) -> list[str]:
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e",
        "EVOLVE_DEBUG=1",
        "-v",
        f"{_install_source(workspace)}:/repo:ro",
        "-v",
        f"{workspace}:/workspace",
        "-v",
        f"{_claude_home(workspace)}:{CONTAINER_CLAUDE_DIR}",
        SANDBOX_IMAGE,
    ]
    return cmd


def _install_evolve_lite(workspace: Path) -> None:
    """Run the real installer inside the image, from the mounted repo.

    This is the documented install from a clone (``/repo`` is the copy
    ``_copy_install_source`` made, not the checkout). It registers ``/repo`` as the
    ``evolve-marketplace`` and installs ``evolve-lite`` from it, writes
    ``.evolve/EVOLVE.md`` and the CLAUDE.md import line into the workspace, and
    puts ``audit_recall.py`` and ``adapt_memory.py`` under ``~/.claude/evolve-lite/``.
    Running it on the host instead would register host paths the container cannot
    see, and ``claude --plugin-dir`` alone would load the skills but none of the rest.
    """
    result = subprocess.run(
        _docker_run_prefix(workspace)
        + ["bash", "/repo/platform-integrations/install.sh", "install", "--platform", "claude", "--dir", "/workspace"],
        capture_output=True,
        text=True,
        timeout=PREFLIGHT_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"claude install failed\nstdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    assert "Claude plugin installed via CLI" in result.stdout, (
        f"install.sh did not install the plugin through the claude CLI\nstdout:\n{result.stdout[-2000:]}"
    )


def _run_sandbox_prompt(workspace: Path, prompt: str) -> subprocess.CompletedProcess:
    cmd = _docker_run_prefix(workspace) + [
        "claude",
        "--dangerously-skip-permissions",
        "--output-format",
        "json",
        "-p",
        prompt,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)


def _session_id(result: subprocess.CompletedProcess) -> str:
    """The session id from ``claude -p --output-format json``, which is also its transcript's stem."""
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"claude did not print a JSON result: {exc}\nstdout:\n{result.stdout[-2000:]}") from exc
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    assert isinstance(session_id, str) and session_id, f"no session_id in claude's JSON result: {payload!r}"
    return session_id


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


def _frontmatter_value(text: str, key: str) -> str | None:
    """Return a top-level ``key: value`` from a leading ``---`` frontmatter block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        name, sep, value = line.partition(":")
        if sep and name.strip() == key:
            return value.strip().strip("'\"")
    return None


def _mirrored_entities(entities_dir: Path) -> dict[str, Path]:
    """Entities adapt-memory mirrored from native memory, keyed by their ``<type>/<name>`` id."""
    mirrored: dict[str, Path] = {}
    if not entities_dir.is_dir():
        return mirrored
    for path in sorted(entities_dir.glob("*/*.md")):
        if path.parent.name == "subscribed":
            continue
        if _frontmatter_value(path.read_text(encoding="utf-8"), "source") == "native-memory":
            mirrored[f"{path.parent.name}/{path.stem}"] = path
    return mirrored


@pytest.mark.e2e
def test_claude_sandbox_installs_evolve_lite(sandbox_workspace):
    """The installed plugin loads in the image, and the files EVOLVE.md names exist.

    Needs the image but no credentials and no model call. If this fails, the
    three-session test cannot pass: either Claude never sees EVOLVE.md, or the
    skill and scripts it points at are missing.
    """
    claude_md = (sandbox_workspace / "CLAUDE.md").read_text(encoding="utf-8")
    assert CLAUDE_IMPORT_LINE in claude_md.splitlines(), f"CLAUDE.md has no {CLAUDE_IMPORT_LINE} import:\n{claude_md}"
    assert (sandbox_workspace / ".evolve" / "EVOLVE.md").is_file()

    plugins = subprocess.run(
        _docker_run_prefix(sandbox_workspace) + ["claude", "plugin", "list"],
        capture_output=True,
        text=True,
        timeout=PREFLIGHT_TIMEOUT_SECONDS,
    )
    assert plugins.returncode == 0, f"claude plugin list exited {plugins.returncode}\nstderr:\n{plugins.stderr[-2000:]}"
    listing = plugins.stdout
    assert "evolve-lite@evolve-marketplace" in listing, f"evolve-lite is not installed:\n{listing}"
    assert "failed to load" not in listing and "enabled" in listing, f"evolve-lite does not load:\n{listing}"

    files = subprocess.run(
        _docker_run_prefix(sandbox_workspace)
        + ["sh", "-c", "test -f ~/.claude/evolve-lite/audit_recall.py && test -f ~/.claude/evolve-lite/adapt_memory.py"],
        capture_output=True,
        text=True,
        timeout=PREFLIGHT_TIMEOUT_SECONDS,
    )
    assert files.returncode == 0, "~/.claude/evolve-lite/audit_recall.py or adapt_memory.py is missing inside the container"


@pytest.mark.e2e
def test_claude_save_then_recall_flow(sandbox_ready, sandbox_workspace):
    """Session 1 saves and mirrors a memory, session 2 recalls it, session 3 records influence."""
    del sandbox_ready  # only used for its skip side effect

    project_dir = _claude_home(sandbox_workspace) / "projects" / CONTAINER_PROJECT_SLUG
    memory_dir = project_dir / "memory"
    entities_dir = sandbox_workspace / ".evolve" / "entities"

    # --- Session 1: location query — dead ends, recovery, then a saved memory ---
    log.info("session 1: running location query in sandbox...")
    t0 = time.time()
    result1 = _run_sandbox_prompt(
        sandbox_workspace,
        (
            "Where was the photo @sample.jpg taken? Use EXIF metadata. "
            "Then remember for future sessions how to read EXIF metadata from photos "
            "in this project, including which tools did and did not work."
        ),
    )
    log.info(f"session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, f"session 1 exited {result1.returncode}\nstderr:\n{result1.stderr[-2000:]}"

    native_memories = [p for p in memory_dir.glob("*.md") if p.name != "MEMORY.md"]
    assert native_memories, f"session 1 saved no native memory under {memory_dir}\nstdout:\n{result1.stdout[-2000:]}"

    mirrored = _mirrored_entities(entities_dir)
    assert mirrored, (
        f"session 1 saved a native memory but nothing was mirrored into {entities_dir}/<type>/, "
        f"so the EVOLVE.md adapt-memory step did not run\nstdout:\n{result1.stdout[-2000:]}"
    )
    for entity_id in mirrored:
        assert entity_id.split("/", 1)[0] in NATIVE_MEMORY_TYPES, f"{entity_id} is not under a native memory type"
    session1_ids = set(mirrored)
    log.info(f"session 1: mirrored {sorted(session1_ids)}")

    # --- Session 2: focal length query — native memory should bring session 1 back ---
    log.info("session 2: running focal length query in sandbox...")
    t1 = time.time()
    result2 = _run_sandbox_prompt(
        sandbox_workspace,
        "what focal length was used to take the photo @sample.jpg. use exif metadata",
    )
    log.info(f"session 2: exited {result2.returncode} after {time.time() - t1:.0f}s")
    assert result2.returncode == 0, f"session 2 exited {result2.returncode}\nstderr:\n{result2.stderr[-2000:]}"

    session2_id = _session_id(result2)
    session2_transcript = project_dir / f"{session2_id}.jsonl"
    assert session2_transcript.is_file(), f"no native transcript for session 2 at {session2_transcript}"

    commands = _bash_commands(session2_transcript)
    joined = "\n".join(commands).lower()

    # Recall ideally steers Claude away from `exiftool`, the one tool definitively
    # absent from the sandbox (not installed, can't be pip-installed). This is a
    # model-behaviour signal, not an evolve contract: the memory text is
    # model-authored and may legitimately say "pip install piexif" without ruling
    # exiftool out. Log it for triage; the recall audit assertions below are what
    # actually verify the recall path.
    if re.search(r"\bexiftool\b", joined):
        log.warning("session 2 still invoked exiftool despite the recalled memory:\n" + "\n".join(commands))
    else:
        log.info(f"session 2: none of the {len(commands)} bash commands invoked exiftool")

    # --- Usage provenance: audit.log should record recall ---
    audit_log = sandbox_workspace / ".evolve" / "audit.log"
    assert audit_log.is_file(), (
        f"{audit_log} was not created — session 2 never ran ~/.claude/evolve-lite/audit_recall.py\nstdout:\n{result2.stdout[-2000:]}"
    )

    events = []
    for line in audit_log.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

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
        assert event.get("verdict") in {"followed", "contradicted", "not_applicable", "insufficient_evidence"}
        assert event.get("evidence"), f"influence event missing evidence: {event}"
