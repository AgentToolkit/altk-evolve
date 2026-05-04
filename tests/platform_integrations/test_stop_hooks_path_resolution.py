"""Tests that both Stop hooks agree on where the saved transcript lives.

Issue #246: ``learn/scripts/on_stop.py`` used to hardcode
``.evolve/trajectories/...`` while ``save-trajectory/scripts/on_stop.py``
resolved the path from env. When ``EVOLVE_DIR`` was set the two hooks
disagreed and the learn skill read a non-existent file.

These tests invoke each hook as a subprocess with a synthetic stdin payload
and assert the path emitted (save-trajectory prints it on stdout; learn
embeds it in the ``reason`` field) matches the shared resolver for the
``EVOLVE_DIR``-set and default scenarios.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_REPO_ROOT = Path(__file__).parent.parent.parent
_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite"
LEARN_ON_STOP = _PLUGIN_ROOT / "skills/learn/scripts/on_stop.py"
SAVE_TRAJ_ON_STOP = _PLUGIN_ROOT / "skills/save-trajectory/scripts/on_stop.py"

_SESSION_ID = "abc-123"
_EXPECTED_FILENAME = f"claude-transcript_{_SESSION_ID}.jsonl"


def _run_hook(script, cwd, env_overrides, transcript_path):
    """Run a Stop hook with a synthetic stdin payload and return CompletedProcess."""
    env = {k: v for k, v in os.environ.items() if k not in {"EVOLVE_DIR", "CLAUDE_PROJECT_ROOT"}}
    env.update(env_overrides)
    payload = {"transcript_path": str(transcript_path), "stop_hook_active": False}
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
        check=True,
    )


def _learn_reason_path(stdout):
    """Extract the path from the learn hook's `reason` field."""
    data = json.loads(stdout)
    reason = data["reason"]
    marker = "The saved trajectory path is: "
    assert marker in reason, f"marker missing in reason: {reason!r}"
    return reason.split(marker, 1)[1].strip()


def _save_trajectory_path(stdout):
    """save-trajectory prints `Trajectory saved: <path>` on stdout."""
    line = stdout.strip().splitlines()[-1]
    prefix = "Trajectory saved: "
    assert line.startswith(prefix), f"unexpected stdout: {stdout!r}"
    return line[len(prefix) :]


@pytest.fixture
def fake_transcript(tmp_path):
    """Create a fake live transcript file matching Claude Code's layout."""
    src = tmp_path / "projects" / "fake-project" / f"{_SESSION_ID}.jsonl"
    src.parent.mkdir(parents=True)
    src.write_text('{"type":"assistant","content":"hi"}\n')
    return src


# ---------------------------------------------------------------------------
# learn/on_stop.py — emits the resolved path in its `reason` field
# ---------------------------------------------------------------------------


def test_learn_uses_evolve_dir(tmp_path, fake_transcript):
    custom = tmp_path / "my-evolve"
    result = _run_hook(
        LEARN_ON_STOP,
        cwd=tmp_path,
        env_overrides={"EVOLVE_DIR": str(custom)},
        transcript_path=fake_transcript,
    )
    path = _learn_reason_path(result.stdout)
    assert path == str((custom / "trajectories" / _EXPECTED_FILENAME).resolve())


def test_learn_defaults_to_cwd_evolve(tmp_path, fake_transcript):
    result = _run_hook(
        LEARN_ON_STOP,
        cwd=tmp_path,
        env_overrides={},
        transcript_path=fake_transcript,
    )
    path = _learn_reason_path(result.stdout)
    expected = (tmp_path / ".evolve" / "trajectories" / _EXPECTED_FILENAME).resolve()
    assert path == str(expected)


# ---------------------------------------------------------------------------
# save-trajectory/on_stop.py — still resolves to the same locations
# ---------------------------------------------------------------------------


def test_save_trajectory_uses_evolve_dir(tmp_path, fake_transcript):
    custom = tmp_path / "my-evolve"
    result = _run_hook(
        SAVE_TRAJ_ON_STOP,
        cwd=tmp_path,
        env_overrides={"EVOLVE_DIR": str(custom)},
        transcript_path=fake_transcript,
    )
    written = Path(_save_trajectory_path(result.stdout))
    assert written == (custom / "trajectories" / _EXPECTED_FILENAME).resolve()
    assert written.is_file()


def test_save_trajectory_defaults_to_cwd_evolve(tmp_path, fake_transcript):
    result = _run_hook(
        SAVE_TRAJ_ON_STOP,
        cwd=tmp_path,
        env_overrides={},
        transcript_path=fake_transcript,
    )
    written = Path(_save_trajectory_path(result.stdout))
    assert written == (tmp_path / ".evolve" / "trajectories" / _EXPECTED_FILENAME).resolve()
    assert written.is_file()


# ---------------------------------------------------------------------------
# Parity — the two hooks must agree on the same path for the same session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_fn",
    [
        pytest.param(lambda tmp: {"EVOLVE_DIR": str(tmp / "my-evolve")}, id="evolve-dir"),
        pytest.param(lambda tmp: {}, id="default"),
    ],
)
def test_hooks_agree_on_path(tmp_path, fake_transcript, env_fn):
    env = env_fn(tmp_path)
    save_result = _run_hook(SAVE_TRAJ_ON_STOP, tmp_path, env, fake_transcript)
    learn_result = _run_hook(LEARN_ON_STOP, tmp_path, env, fake_transcript)
    written = _save_trajectory_path(save_result.stdout)
    announced = _learn_reason_path(learn_result.stdout)
    assert written == announced
