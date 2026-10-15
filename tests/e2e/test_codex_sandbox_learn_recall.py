"""End-to-end test of the evolve-lite save + recall flow in the Codex sandbox.

Codex ships no ``learn`` or ``recall`` skill and installs no hooks. Both halves
of the loop are driven by ``EVOLVE.md`` instead: ``install.sh`` copies it to
``~/.codex/evolve-lite/EVOLVE.md`` and injects a one-line pointer into
``~/.codex/AGENTS.md`` telling the agent to read it at the start of every
conversation. EVOLVE.md asks the agent to:

  * recall — as its first action on a non-trivial task, check
    ``./.evolve/entities/`` and record whatever it opened with
    ``python3 ~/.codex/evolve-lite/audit_recall.py <type>/<name> ...``, which
    appends a ``recall`` event to ``.evolve/audit.log``;
  * save — near the end of a task, write a durable lesson as
    ``./.evolve/entities/<type>/<slug>.md``.

``test_codex_sandbox_delivers_evolve_md`` checks that wiring without
credentials or a model call. ``test_codex_save_then_recall_flow`` runs three
sequential Codex sessions against the Dockerized Codex sandbox:

  1. Session 1 performs an EXIF task (the sandbox has neither exiftool nor
     Pillow), saves what it learned as an entity under
     ``.evolve/entities/<type>/``, and saves a trajectory.
  2. Session 2 asks a related EXIF question. Following EVOLVE.md, it recalls
     the session-1 entity and records it with audit_recall.py.
  3. Session 3 runs the offline provenance skill so the recall gets an
     influence verdict.

Requires Docker and the ``evolve-codex-sandbox`` image built
(``just sandbox-build codex``). The three-session test also needs Codex
credentials exported in the environment.
"""

import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterable

import pytest


log = logging.getLogger(__name__)


SANDBOX_IMAGE = "evolve-codex-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_TIMEOUT_SECONDS = 600
PREFLIGHT_TIMEOUT_SECONDS = 120
FORWARDED_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "CODEX_MODEL",
)
CODEX_PROVIDER_ENV_KEY_VAR = "CODEX_MODEL_PROVIDER_ENV_KEY"
CONTAINER_NAME_PREFIX = "evolve-codex-e2e"

# The marker install.sh puts on the AGENTS.md pointer line (MANAGED_MARKER).
EVOLVE_POINTER_MARKER = "<!-- evolve-lite:managed -->"
# The entity types EVOLVE.md tells the agent to save under.
EVOLVE_ENTITY_TYPES = {"user", "feedback", "project", "reference"}


@pytest.fixture(scope="session")
def codex_sandbox_image():
    """Skip if Docker or the Codex sandbox image isn't available."""
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

    return True


@pytest.fixture(scope="session")
def codex_sandbox_ready(codex_sandbox_image):
    """Additionally skip if Codex credentials aren't available."""
    del codex_sandbox_image

    credential_env_var = os.environ.get(CODEX_PROVIDER_ENV_KEY_VAR, "OPENAI_API_KEY")
    if not os.environ.get(credential_env_var):
        pytest.skip(f"{credential_env_var} not set in environment")

    return True


def _require_consistent_provider_env() -> None:
    """Fail with the real cause when the gateway env vars are only half set.

    Gateway routing needs CODEX_MODEL_PROVIDER and a base URL together: the
    ``[model_providers.<name>]`` table is only written when both are set. Half
    a pair sends the run to the wrong endpoint. With a provider and no base
    URL, even the workspace fixture's ``codex plugin`` commands fail ("Model
    provider `<name>` not found"), so this runs wherever config.toml is
    written, not just in the credentialed test.
    """
    provider = os.environ.get("CODEX_MODEL_PROVIDER")
    provider_base_url = os.environ.get("CODEX_MODEL_PROVIDER_BASE_URL")

    # Base URL alone is dropped entirely — it never reaches config.toml without a
    # provider, and it is not forwarded into the container either — so the run
    # would quietly use the default OpenAI endpoint instead of the gateway.
    if provider_base_url and not provider:
        pytest.fail(
            "CODEX_MODEL_PROVIDER_BASE_URL is set but CODEX_MODEL_PROVIDER is not — "
            "the base URL would be ignored and Codex would use its default endpoint. "
            "Set both for gateway routing, or unset the base URL."
        )

    # Provider alone writes `model_provider = "<name>"` with no matching table, so
    # Codex falls back to its default endpoint and dies with an opaque 401.
    base_url = provider_base_url or os.environ.get("OPENAI_BASE_URL")
    if provider and not base_url:
        pytest.fail(
            f"CODEX_MODEL_PROVIDER={provider!r} is set but neither "
            "CODEX_MODEL_PROVIDER_BASE_URL nor OPENAI_BASE_URL is — Codex would be "
            "pointed at an undefined provider and fail to authenticate. Set a base "
            "URL, or unset CODEX_MODEL_PROVIDER to use the default OpenAI provider."
        )


@pytest.fixture
def codex_workspace(tmp_path, codex_sandbox_image):
    """Copy demo/workspace and install the Codex plugin into it."""
    del codex_sandbox_image  # plugin registration runs inside the image

    src = REPO_ROOT / "demo" / "workspace"
    workspace = tmp_path / "workspace"
    shutil.copytree(src, workspace, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))

    _install_codex_plugin(workspace)
    _register_codex_plugin_for_container(workspace)
    return workspace


def _install_codex_plugin(workspace: Path) -> None:
    """Run the real installer with HOME pointed at the container's home.

    The installer writes its global artifacts under ``~/.codex/``: the AGENTS.md
    pointer, EVOLVE.md and audit_recall.py. Run with the developer's own HOME,
    it would edit their real ``~/.codex/AGENTS.md``, and the container would see
    none of it. The container entrypoint sets ``HOME=CODEX_HOME=/codex-home``,
    and ``.codex-home`` is what gets mounted there. So installing with
    ``HOME=.codex-home`` puts EVOLVE.md and audit_recall.py exactly where the
    ``~/.codex/evolve-lite/...`` paths named by the pointer and by EVOLVE.md
    resolve inside the container.

    One file still has to move. Codex reads its global AGENTS.md from
    ``$CODEX_HOME/AGENTS.md``, which is ``/codex-home/AGENTS.md`` here, not the
    ``/codex-home/.codex/AGENTS.md`` the installer wrote. Without the copy the
    pointer never reaches the model, so EVOLVE.md is never read and nothing is
    saved or recalled.
    """
    codex_home = workspace / ".codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)

    install_script = REPO_ROOT / "platform-integrations" / "install.sh"
    result = subprocess.run(
        ["bash", str(install_script), "install", "--platform", "codex", "--dir", str(workspace)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(codex_home)},
    )
    assert result.returncode == 0, f"codex install failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    installed_agents_md = codex_home / ".codex" / "AGENTS.md"
    assert EVOLVE_POINTER_MARKER in installed_agents_md.read_text(encoding="utf-8"), (
        f"install.sh did not write the EVOLVE.md pointer to {installed_agents_md}"
    )
    shutil.copyfile(installed_agents_md, codex_home / "AGENTS.md")


def _toml_str(value: str) -> str:
    return json.dumps(value)


def _codex_config_lines() -> list[str]:
    _require_consistent_provider_env()
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
    """Install the plugin with the image's own ``codex plugin`` commands.

    Recall and save need no skill, since EVOLVE.md drives them, but the
    save-trajectory and provenance skills do. Registering through the CLI keeps
    this test independent of how a given Codex release lays out its plugin cache
    and marketplace config. The hand-built cache this test used to write loads
    on 0.122.0 but not on 0.160.1, which wants ``source_type = "local"`` on the
    marketplace and finds no skills in the flattened copy.

    The marketplace is the ``.agents/plugins/marketplace.json`` that install.sh
    wrote into the workspace, added at the path the container sees it:
    /workspace. Model and provider settings go into config.toml first; the
    ``codex plugin`` commands add their tables alongside them.
    """
    codex_home = workspace / ".codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text("\n".join(_codex_config_lines()), encoding="utf-8")

    marketplace_json = workspace / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_json.read_text(encoding="utf-8"))["name"]
    register = f"codex plugin marketplace add /workspace && codex plugin add {shlex.quote(f'evolve-lite@{marketplace}')}"
    result = _run_in_container(workspace, ["sh", "-c", register], PREFLIGHT_TIMEOUT_SECONDS)
    assert result.returncode == 0, f"codex plugin registration failed\nstdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"


def _forwarded_env_vars() -> Iterable[str]:
    yield from FORWARDED_ENV_VARS
    provider_env_key = os.environ.get(CODEX_PROVIDER_ENV_KEY_VAR)
    if provider_env_key:
        yield provider_env_key


def _docker_run_prefix(workspace: Path, name: str) -> list[str]:
    codex_home = workspace / ".codex-home"
    cmd = ["docker", "run", "--rm", "--name", name]
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
    ]
    return cmd


def _run_in_container(workspace: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run ``args`` in a fresh sandbox container; remove the container on timeout.

    On a timeout, subprocess kills only the ``docker run`` client. That does
    not stop the container, and ``--rm`` only fires once it exits, so a hung
    Codex session would keep running after the test failed. Naming each
    container lets the timeout path remove it.
    """
    name = f"{CONTAINER_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    try:
        return subprocess.run(
            _docker_run_prefix(workspace, name) + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False, timeout=PREFLIGHT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            log.warning(f"could not remove timed-out container {name}")
        raise


def _run_codex_prompt(workspace: Path, prompt: str) -> subprocess.CompletedProcess:
    args = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        "/workspace",
        prompt,
    ]
    return _run_in_container(workspace, args, SESSION_TIMEOUT_SECONDS)


def _audit_events(evolve_dir: Path) -> list[dict]:
    audit_log = evolve_dir / "audit.log"
    if not audit_log.is_file():
        return []
    return [json.loads(line) for line in audit_log.read_text().splitlines() if line.strip()]


def _recall_events(evolve_dir: Path, since: int = 0) -> list[dict]:
    """``recall`` rows appended to audit.log after its first ``since`` rows."""
    return [event for event in _audit_events(evolve_dir)[since:] if event.get("event") == "recall"]


def _trajectory_session_ids(paths: Iterable[Path]) -> set[str]:
    """The ``session_id`` values the given saved trajectories carry.

    In the container this field is the only way provenance can tie a recall
    to its trajectory: ``--ephemeral`` sessions write no Codex rollout files,
    so its native-transcript lookup has nothing to find.
    """
    session_ids: set[str] = set()
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("session_id"), str):
            session_ids.add(data["session_id"])
    return session_ids


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


def _saved_entities(entities_dir: Path) -> dict[str, Path]:
    """Entities the agent saved per EVOLVE.md, keyed by their ``<type>/<name>`` id.

    Only ``<type>/<name>.md`` files directly under a type directory count.
    ``subscribed/`` holds other repos' entities, not this agent's saves.
    """
    saved: dict[str, Path] = {}
    if not entities_dir.is_dir():
        return saved
    for path in sorted(entities_dir.glob("*/*.md")):
        if path.parent.name == "subscribed":
            continue
        saved[f"{path.parent.name}/{path.stem}"] = path
    return saved


@pytest.mark.e2e
def test_codex_sandbox_delivers_evolve_md(codex_workspace):
    """The EVOLVE.md pointer reaches the model, and the files it names exist.

    Needs the image but no credentials and no model call: ``codex debug
    prompt-input`` renders the prompt input Codex would send, through the
    image's real entrypoint. If this fails, the three-session test cannot pass:
    Codex is never told to read EVOLVE.md, or cannot see the skills it is asked
    to invoke.
    """
    prompt_input = _run_in_container(codex_workspace, ["codex", "debug", "prompt-input", "hello"], PREFLIGHT_TIMEOUT_SECONDS)
    assert prompt_input.returncode == 0, (
        f"codex debug prompt-input exited {prompt_input.returncode}\n"
        f"stdout:\n{prompt_input.stdout[-2000:]}\nstderr:\n{prompt_input.stderr[-2000:]}"
    )
    assert EVOLVE_POINTER_MARKER in prompt_input.stdout, (
        "the AGENTS.md pointer to EVOLVE.md is not in Codex's prompt input, so the "
        f"agent will never read EVOLVE.md\nstdout:\n{prompt_input.stdout[-2000:]}"
    )
    assert "~/.codex/evolve-lite/EVOLVE.md" in prompt_input.stdout
    for skill in ("evolve-lite:save-trajectory", "evolve-lite:provenance"):
        assert skill in prompt_input.stdout, f"the {skill} skill is not visible to Codex\nstdout:\n{prompt_input.stdout[-2000:]}"

    # The pointer and EVOLVE.md name these paths with `~`. Resolve them with the
    # container's own HOME rather than assuming where it points.
    files = _run_in_container(
        codex_workspace,
        ["sh", "-c", "test -f ~/.codex/evolve-lite/EVOLVE.md && test -f ~/.codex/evolve-lite/audit_recall.py"],
        PREFLIGHT_TIMEOUT_SECONDS,
    )
    assert files.returncode == 0, (
        "~/.codex/evolve-lite/EVOLVE.md or audit_recall.py is missing inside the "
        f"container\nstdout:\n{files.stdout[-2000:]}\nstderr:\n{files.stderr[-2000:]}"
    )


@pytest.mark.e2e
def test_codex_save_then_recall_flow(codex_sandbox_ready, codex_workspace):
    """Session 1 saves an entity, session 2 recalls it, session 3 records influence."""
    del codex_sandbox_ready

    evolve_dir = codex_workspace / ".evolve"
    entities_dir = evolve_dir / "entities"
    trajectories_dir = evolve_dir / "trajectories"

    log.info("codex session 1: running seed task, then save-trajectory...")
    t0 = time.time()
    result1 = _run_codex_prompt(
        codex_workspace,
        (
            "Where was the photo @sample.jpg taken? Use EXIF metadata. "
            "Before you finish, save what you learned about reading EXIF metadata in "
            "this environment as an Evolve memory entity, the way your Evolve memory "
            "instructions describe. "
            "When done, invoke the evolve-lite save-trajectory skill."
        ),
    )
    log.info(f"codex session 1: exited {result1.returncode} after {time.time() - t0:.0f}s")
    assert result1.returncode == 0, (
        f"session 1 exited {result1.returncode}\nstdout:\n{result1.stdout[-2000:]}\nstderr:\n{result1.stderr[-2000:]}"
    )

    assert trajectories_dir.is_dir(), f"{trajectories_dir} was not created"
    trajectories = list(trajectories_dir.glob("*.json"))
    assert trajectories, f"no Codex trajectory JSON files found in {trajectories_dir}"

    saved = _saved_entities(entities_dir)
    assert saved, (
        f"session 1 saved no entity under {entities_dir}/<type>/, so the EVOLVE.md Save step did not run\nstdout:\n{result1.stdout[-2000:]}"
    )
    for entity_id, path in saved.items():
        text = path.read_text(encoding="utf-8")
        entity_type = entity_id.split("/", 1)[0]
        assert entity_type in EVOLVE_ENTITY_TYPES, f"{entity_id} is not under one of the EVOLVE.md types {sorted(EVOLVE_ENTITY_TYPES)}"
        assert _frontmatter_value(text, "type") == entity_type, f"{path} frontmatter type does not match its directory:\n{text}"
        assert _frontmatter_value(text, "trigger"), f"{path} has no trigger, so recall has nothing to match:\n{text}"

    # Only rows that session 2 appends count as its recall. A recall row that
    # session 1 wrote (of the entity it had just saved, say) must not pass.
    audit_rows_before_session2 = len(_audit_events(evolve_dir))

    log.info("codex session 2: running related task to exercise EVOLVE.md recall...")
    t1 = time.time()
    result2 = _run_codex_prompt(
        codex_workspace,
        (
            "What focal length was used to take the photo @sample.jpg? Use EXIF metadata. "
            "When done, invoke the evolve-lite save-trajectory skill."
        ),
    )
    log.info(f"codex session 2: exited {result2.returncode} after {time.time() - t1:.0f}s")
    assert result2.returncode == 0, (
        f"session 2 exited {result2.returncode}\nstdout:\n{result2.stdout[-2000:]}\nstderr:\n{result2.stderr[-2000:]}"
    )

    session2_trajectories = {path for path in trajectories_dir.glob("*.json")} - set(trajectories)
    assert session2_trajectories, f"no Codex trajectory saved for session 2 in {trajectories_dir}"

    recall_events = _recall_events(evolve_dir, since=audit_rows_before_session2)
    assert recall_events, (
        "session 2 recorded no recall audit event: it skipped the EVOLVE.md recall step "
        f"(it never ran ~/.codex/evolve-lite/audit_recall.py). all events: {_audit_events(evolve_dir)}\n"
        f"stdout:\n{result2.stdout[-2000:]}"
    )
    task_recall_event = recall_events[-1]
    task_session_id = task_recall_event["session_id"]
    task_recalled_ids = set(task_recall_event.get("entities", []))
    assert task_recalled_ids & set(saved), f"task recall ids {task_recalled_ids} did not include saved ids {set(saved)}"

    # Session 3's provenance can only judge this recall if session 2's
    # trajectory carries the same session id. Check it here, so a missing id
    # fails as a session-2 problem instead of an unexplained session-3 one.
    trajectory_session_ids = _trajectory_session_ids(session2_trajectories)
    assert task_session_id in trajectory_session_ids, (
        f"session 2 logged its recall under session id {task_session_id!r}, but its trajectory carries "
        f"{sorted(trajectory_session_ids) or 'no session_id'}, so provenance cannot match the two. "
        "audit_recall.py takes the id from CODEX_THREAD_ID; save-trajectory has to write the same id."
    )

    log.info("codex session 3: running offline provenance analysis...")
    t2 = time.time()
    result3 = _run_codex_prompt(
        codex_workspace,
        (
            "Run the evolve-lite provenance skill now. Analyze the saved trajectories and "
            "the recall events in .evolve/audit.log. Record influence verdicts "
            f"for recalled entities in session {task_session_id}, the focal-length "
            "photo session. Do not modify source files."
        ),
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
    allowed_verdicts = {"followed", "contradicted", "not_applicable"}
    assert any(event.get("verdict") in allowed_verdicts for event in influence_events), (
        f"no recalled entity was assessed with an allowed verdict. influence events: {influence_events}"
    )
    for event in influence_events:
        assert event.get("verdict") in allowed_verdicts
        assert event.get("evidence"), f"influence event missing evidence: {event}"
