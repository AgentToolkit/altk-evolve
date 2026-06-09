"""Unit tests for the self-contained recall-audit script (audit_recall.py).

The script is run as a subprocess (as a model would invoke it) in a temp cwd,
exercising session-id resolution, the appended JSON row, the self-minted-UUID
echo, the no-args no-op, and the EVOLVE_DIR override.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).parent.parent.parent / "plugin-source" / "lib" / "audit_recall.py"


def _run(cwd, args, env_overrides):
    """Run audit_recall.py with a controlled environment.

    Start from a copy of os.environ with both session vars cleared, then apply
    `env_overrides` so each test gets exactly the session state it intends.
    """
    env = {**os.environ}
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("CODEX_THREAD_ID", None)
    env.pop("EVOLVE_DIR", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _read_rows(log_path):
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


@pytest.mark.platform_integrations
class TestAuditRecall:
    def test_claude_session_id_no_echo(self, tmp_path):
        result = _run(tmp_path, ["mem1.md", "mem2.md"], {"CLAUDE_CODE_SESSION_ID": "abc"})

        log = tmp_path / ".evolve" / "audit.log"
        rows = _read_rows(log)
        assert len(rows) == 1
        row = rows[0]
        assert row["event"] == "recall"
        assert row["session_id"] == "abc"
        assert row["entities"] == ["mem1.md", "mem2.md"]
        assert row["ts"]
        assert "evolve-session:" not in result.stdout

    def test_codex_thread_id_no_echo(self, tmp_path):
        result = _run(tmp_path, ["mem1.md"], {"CODEX_THREAD_ID": "xyz"})

        rows = _read_rows(tmp_path / ".evolve" / "audit.log")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "xyz"
        assert rows[0]["entities"] == ["mem1.md"]
        assert "evolve-session:" not in result.stdout

    def test_self_minted_uuid_is_echoed(self, tmp_path):
        result = _run(tmp_path, ["mem1.md"], {})

        rows = _read_rows(tmp_path / ".evolve" / "audit.log")
        assert len(rows) == 1
        minted = rows[0]["session_id"]

        echo_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("evolve-session:")]
        assert len(echo_lines) == 1
        assert echo_lines[0] == f"evolve-session: {minted}"

    def test_no_args_writes_nothing(self, tmp_path):
        result = _run(tmp_path, [], {"CLAUDE_CODE_SESSION_ID": "abc"})

        assert result.returncode == 0
        assert not (tmp_path / ".evolve").exists()

    def test_respects_evolve_dir_override(self, tmp_path):
        evolve_dir = tmp_path / "custom_evolve"
        _run(tmp_path, ["mem1.md"], {"CLAUDE_CODE_SESSION_ID": "abc", "EVOLVE_DIR": str(evolve_dir)})

        assert not (tmp_path / ".evolve").exists()
        rows = _read_rows(evolve_dir / "audit.log")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "abc"

    def test_appends_across_runs(self, tmp_path):
        _run(tmp_path, ["mem1.md"], {"CLAUDE_CODE_SESSION_ID": "abc"})
        _run(tmp_path, ["mem2.md"], {"CLAUDE_CODE_SESSION_ID": "abc"})

        rows = _read_rows(tmp_path / ".evolve" / "audit.log")
        assert [r["entities"] for r in rows] == [["mem1.md"], ["mem2.md"]]
