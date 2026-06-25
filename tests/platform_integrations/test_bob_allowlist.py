"""Bob install auto-allowlists ONLY the recall-audit shell command.

Bob (a Gemini-CLI fork) prefix-matches its shell allowlist and splits chained
commands, so allowlisting the exact recall-audit command can't widen. The
installer merges that single rule into ``tools.allowed`` of the user's GLOBAL
``~/.bob/settings.json`` so the recall-audit step stops prompting every session.
Entity read/write prompts are deliberately left intact, and blanket auto-accept
is never enabled. Crucially — unlike the Claude variant — uninstall must NEVER
delete the settings file: it is the user's own config, not an evolve artifact.
"""

import json

import pytest

# The exact (and only) allow-rule the installer merges into Bob's settings.
BOB_AUDIT_ALLOW = "run_shell_command(python3 ~/.bob/evolve-lite/audit_recall.py)"


def _allowed(settings_path):
    """The tools.allowed list from a settings.json (empty list if absent)."""
    if not settings_path.is_file():
        return []
    return json.loads(settings_path.read_text()).get("tools", {}).get("allowed", [])


@pytest.mark.platform_integrations
class TestBobAuditAllowlist:
    """Install pre-authorizes only the recall-audit command; uninstall reverses it."""

    def test_install_merges_audit_allow_rule(self, install_runner, bob_settings_file):
        install_runner.run("install", platform="bob")
        assert BOB_AUDIT_ALLOW in _allowed(bob_settings_file)

    def test_install_allowlists_nothing_else(self, install_runner, bob_settings_file):
        """Exactly one rule — we never broadly allow file tools or auto-accept."""
        install_runner.run("install", platform="bob")
        assert _allowed(bob_settings_file) == [BOB_AUDIT_ALLOW]

    def test_reinstall_does_not_duplicate_rule(self, install_runner, bob_settings_file):
        install_runner.run("install", platform="bob")
        install_runner.run("install", platform="bob")
        assert _allowed(bob_settings_file).count(BOB_AUDIT_ALLOW) == 1

    def test_install_preserves_existing_settings_and_rules(self, install_runner, bob_settings_file):
        """A pre-existing unrelated allow-rule and other settings keys survive."""
        bob_settings_file.parent.mkdir(parents=True, exist_ok=True)
        bob_settings_file.write_text(
            json.dumps(
                {
                    "ide": {"enabled": True},
                    "tools": {"allowed": ["run_shell_command(git status)"], "autoAccept": False},
                },
                indent=2,
            )
            + "\n"
        )

        install_runner.run("install", platform="bob")

        data = json.loads(bob_settings_file.read_text())
        # Unrelated top-level key preserved.
        assert data["ide"] == {"enabled": True}
        # Unrelated tools sibling preserved (we never flip autoAccept).
        assert data["tools"]["autoAccept"] is False
        allowed = data["tools"]["allowed"]
        # Pre-existing rule preserved and our rule merged in (no duplicates).
        assert "run_shell_command(git status)" in allowed
        assert allowed.count(BOB_AUDIT_ALLOW) == 1

    def test_uninstall_removes_only_evolve_rule(self, install_runner, bob_settings_file):
        """Uninstall drops exactly our rule, leaving user rules + keys intact."""
        bob_settings_file.parent.mkdir(parents=True, exist_ok=True)
        bob_settings_file.write_text(
            json.dumps(
                {"ide": {"enabled": True}, "tools": {"allowed": ["run_shell_command(git status)"]}},
                indent=2,
            )
            + "\n"
        )
        install_runner.run("install", platform="bob")
        install_runner.run("uninstall", platform="bob")

        data = json.loads(bob_settings_file.read_text())
        assert data["ide"] == {"enabled": True}
        assert data["tools"]["allowed"] == ["run_shell_command(git status)"]
        assert BOB_AUDIT_ALLOW not in data["tools"]["allowed"]

    def test_uninstall_never_deletes_settings_file(self, install_runner, bob_settings_file, file_assertions):
        """Even when our rule was the only content, the user's settings file
        must survive uninstall (it is their config, not an evolve artifact)."""
        install_runner.run("install", platform="bob")
        file_assertions.assert_file_exists(bob_settings_file)

        install_runner.run("uninstall", platform="bob")

        # File persists; our now-empty keys are cleaned up to {}.
        file_assertions.assert_file_exists(bob_settings_file)
        assert _allowed(bob_settings_file) == []

    def test_dry_run_writes_no_settings(self, install_runner, bob_settings_file):
        result = install_runner.run("install", platform="bob", dry_run=True)
        assert "DRY RUN" in result.stdout
        assert not bob_settings_file.exists()
