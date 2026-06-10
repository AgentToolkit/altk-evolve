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

    def _seed_bob_chat(self, home, cwd, session_id, *, filename="session-2026-06-10T21-12-d6484b2c.json"):
        """Write a fake Bob session file at ~/.bob/tmp/<sha256(cwd)>/chats/."""
        import hashlib

        project_hash = hashlib.sha256(os.path.realpath(str(cwd)).encode()).hexdigest()
        chats = Path(home) / ".bob" / "tmp" / project_hash / "chats"
        chats.mkdir(parents=True)
        (chats / filename).write_text(
            json.dumps({"sessionId": session_id, "projectHash": project_hash, "messages": []}),
            encoding="utf-8",
        )

    def test_bob_session_id_recovered_from_chat_file(self, tmp_path):
        """Under Bob (BOBSHELL_CLI set), with no Claude/Codex env id, the script
        recovers the real sessionId from ~/.bob/tmp/<sha256(cwd)>/chats/ rather
        than minting one — so provenance can tie the recall to the trajectory."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        sid = "d6484b2c-24f4-474c-8f43-36544e2dbcd8"
        self._seed_bob_chat(home, proj, sid)

        result = _run(proj, ["project/baz"], {"BOBSHELL_CLI": "1", "HOME": str(home), "USERPROFILE": str(home)})

        rows = _read_rows(proj / ".evolve" / "audit.log")
        assert rows[0]["session_id"] == sid
        # A recovered (non-minted) id is not echoed.
        assert "evolve-session:" not in result.stdout

    def test_bob_picks_newest_chat(self, tmp_path):
        """When several Bob sessions exist for the project, the newest (the one
        being written now) wins."""
        import hashlib

        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_bob_chat(home, proj, "old-1111", filename="session-2026-06-10T20-00-old11111.json")
        newest = "new02222-3333-4444-5555-66667777aaaa"
        project_hash = hashlib.sha256(os.path.realpath(str(proj)).encode()).hexdigest()
        chat = home / ".bob" / "tmp" / project_hash / "chats" / "session-2026-06-10T21-30-new02222.json"
        chat.write_text(json.dumps({"sessionId": newest, "messages": []}), encoding="utf-8")
        os.utime(chat, (10**10, 10**10))  # far-future mtime => newest

        result = _run(proj, ["project/baz"], {"BOBSHELL_CLI": "1", "HOME": str(home), "USERPROFILE": str(home)})

        rows = _read_rows(proj / ".evolve" / "audit.log")
        assert rows[0]["session_id"] == newest
        assert "evolve-session:" not in result.stdout

    def test_bob_branch_inert_without_bobshell_cli(self, tmp_path):
        """No BOBSHELL_CLI => the Bob lookup never runs (even with a chat present),
        so the script mints a uuid as before. Keeps the branch inert off-Bob."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_bob_chat(home, proj, "d6484b2c-24f4-474c-8f43-36544e2dbcd8")

        result = _run(proj, ["project/baz"], {"HOME": str(home), "USERPROFILE": str(home)})

        rows = _read_rows(proj / ".evolve" / "audit.log")
        assert rows[0]["session_id"] != "d6484b2c-24f4-474c-8f43-36544e2dbcd8"
        assert f"evolve-session: {rows[0]['session_id']}" in result.stdout  # minted => echoed

    def test_env_session_id_beats_bob_lookup(self, tmp_path):
        """An explicit Claude/Codex env id takes precedence over the Bob file
        lookup (the env id is authoritative when present)."""
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir()
        self._seed_bob_chat(home, proj, "bob-sid-should-not-win")

        _run(
            proj,
            ["project/baz"],
            {"BOBSHELL_CLI": "1", "CODEX_THREAD_ID": "codex-wins", "HOME": str(home), "USERPROFILE": str(home)},
        )

        rows = _read_rows(proj / ".evolve" / "audit.log")
        assert rows[0]["session_id"] == "codex-wins"
