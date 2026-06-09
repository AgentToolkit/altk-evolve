"""
Tests for the Claude platform integration installer behavior.

Claude installs the plugin via marketplace (``claude plugin install``), which
delegates to the claude CLI and copies nothing to the repo. Separately — and
INDEPENDENTLY of whether the CLI is present — the installer performs a per-repo
file delivery so the thin EVOLVE.md actually reaches Claude's context every
session:
  * a COPY of the thin EVOLVE.md at the PER-REPO path ``<repo>/.evolve/EVOLVE.md``,
  * a SINGLE native ``@``-import pointer line (``@.evolve/EVOLVE.md``) injected
    into the PER-REPO ``<repo>/CLAUDE.md`` (the line is its own uninstall handle),
  * the self-contained recall-audit script at the GLOBAL (sandboxed) path
    ``~/.claude/evolve-lite/audit_recall.py`` referenced by that EVOLVE.md.

Some tests control PATH to simulate the CLI being absent, which lets us verify
the marketplace fallback output without needing the actual CLI installed; the
file delivery still runs in that case.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# PATH that contains no claude binary — forces the "CLI not found" fallback path.
_NO_CLAUDE_PATH = "/usr/bin:/bin"

# The single native CLAUDE.md import pointer line (its own uninstall handle).
IMPORT_LINE = "@.evolve/EVOLVE.md"
# A distinctive sentence from the thin EVOLVE.md body that must live in the copy.
EVOLVE_BODY_SENTENCE = "You already have native, self-directed memory"
# A distinctive string from the recall-audit script.
AUDIT_SCRIPT_SENTENCE = "Append a recall-audit row"

# The exact set of allow-rules the installer merges into <repo>/.claude/settings.json.
EXPECTED_ALLOW_RULES = [
    "Bash(python3 ~/.claude/evolve-lite/adapt_memory.py:*)",
    "Bash(python3 ~/.claude/evolve-lite/audit_recall.py:*)",
    "Read(.evolve/**)",
    "Edit(.evolve/**)",
    "Write(.evolve/**)",
]

_REPO_ROOT = Path(__file__).parent.parent.parent
# The rendered Claude adapt-memory skill — its invocation must point at the
# stable global path, not the version-unstable ${CLAUDE_PLUGIN_ROOT} dir.
_RENDERED_ADAPT_SKILL = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite" / "skills/evolve-lite/adapt-memory/SKILL.md"


def _import_lines(text):
    """Return the lines in `text` that carry the managed @-import marker."""
    return [ln for ln in text.splitlines() if IMPORT_LINE in ln]


@pytest.mark.platform_integrations
class TestClaudeInstall:
    """Test the Claude install flow."""

    def test_cli_absent_prompts_download(self, temp_project_dir, install_runner):
        """When claude CLI is absent, user should be told to install it and re-run."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert "claude.ai/download" in result.stdout
        assert "re-run" in result.stdout

    def test_cli_absent_exits_success(self, temp_project_dir, install_runner):
        """Missing claude CLI should warn but not fail the overall install."""
        result = install_runner.run("install", platform="claude", env={"PATH": _NO_CLAUDE_PATH})

        assert result.returncode == 0


@pytest.mark.platform_integrations
@pytest.mark.e2e
class TestClaudeFileDelivery:
    """Test the per-repo EVOLVE.md import-pointer delivery (independent of the CLI)."""

    def test_install_delivers_pointer_evolve_md_and_audit_script(
        self,
        temp_project_dir,
        install_runner,
        file_assertions,
        claude_md_file,
        claude_evolve_md,
        claude_audit_script,
    ):
        """Install injects one @-import line into CLAUDE.md, copies the thin EVOLVE.md, and installs the global audit script."""
        install_runner.run("install", platform="claude")

        # A SINGLE native @-import pointer line is injected into <repo>/CLAUDE.md.
        file_assertions.assert_file_exists(claude_md_file)
        import_lines = _import_lines(claude_md_file.read_text())
        assert len(import_lines) == 1, f"Expected exactly one import line, got {import_lines!r}"
        assert import_lines[0].strip() == IMPORT_LINE

        # A COPY of the thin EVOLVE.md is dropped at <repo>/.evolve/EVOLVE.md.
        file_assertions.assert_file_exists(claude_evolve_md)
        assert EVOLVE_BODY_SENTENCE in claude_evolve_md.read_text()

        # The recall-audit script is installed at the GLOBAL sandboxed path.
        file_assertions.assert_file_exists(claude_audit_script)
        assert AUDIT_SCRIPT_SENTENCE in claude_audit_script.read_text()

    def test_install_is_idempotent_no_duplicate_pointer(self, temp_project_dir, install_runner, claude_md_file):
        """Running install twice must not duplicate the @-import line in CLAUDE.md."""
        install_runner.run("install", platform="claude")
        install_runner.run("install", platform="claude")

        import_lines = _import_lines(claude_md_file.read_text())
        assert len(import_lines) == 1, f"Expected exactly one import line after two installs, got {import_lines!r}"

    def test_install_preserves_existing_claude_md_content(self, temp_project_dir, install_runner, claude_md_file):
        """Injecting the import line must not clobber pre-existing CLAUDE.md content."""
        claude_md_file.write_text("# Project rules\n\nExisting guidance line.\n")
        install_runner.run("install", platform="claude")

        text = claude_md_file.read_text()
        assert "Existing guidance line." in text
        assert len(_import_lines(text)) == 1

    def test_claude_dry_run_does_not_write_files(
        self,
        temp_project_dir,
        install_runner,
        claude_md_file,
        claude_evolve_md,
        claude_audit_script,
    ):
        """Dry-run should report actions without writing any files."""
        result = install_runner.run("install", platform="claude", dry_run=True)

        assert "DRY RUN" in result.stdout
        assert not claude_md_file.exists()
        assert not claude_evolve_md.exists()
        assert not claude_audit_script.exists()

    def test_uninstall_removes_pointer_and_evolve_md_and_audit(
        self,
        temp_project_dir,
        install_runner,
        file_assertions,
        claude_md_file,
        claude_evolve_md,
        claude_audit_script,
    ):
        """Uninstall removes the @-import line, the per-repo EVOLVE.md copy, and the global audit script."""
        install_runner.run("install", platform="claude")
        file_assertions.assert_file_exists(claude_evolve_md)
        file_assertions.assert_file_exists(claude_audit_script)
        assert len(_import_lines(claude_md_file.read_text())) == 1

        install_runner.run("uninstall", platform="claude")

        # No @-import reference remains in CLAUDE.md.
        assert IMPORT_LINE not in claude_md_file.read_text()
        # The placed per-repo EVOLVE.md and the global audit script are gone.
        file_assertions.assert_file_not_exists(claude_evolve_md)
        file_assertions.assert_file_not_exists(claude_audit_script)


@pytest.mark.platform_integrations
class TestClaudeRenderedAdaptSkill:
    """The rendered adapt-memory skill must invoke the stable global path."""

    def test_rendered_skill_uses_stable_path_not_plugin_root(self):
        text = _RENDERED_ADAPT_SKILL.read_text()
        # The version-unstable plugin-root form must be gone entirely.
        assert "${CLAUDE_PLUGIN_ROOT}" not in text
        # The stable, allowlistable global path must be the invocation target.
        assert "python3 ~/.claude/evolve-lite/adapt_memory.py" in text


@pytest.mark.platform_integrations
@pytest.mark.e2e
class TestClaudeAdaptScriptDelivery:
    """The adapt-memory adapter + its lib land at the stable global path."""

    def test_install_ships_adapt_script_and_lib(
        self,
        install_runner,
        file_assertions,
        claude_adapt_script,
        claude_adapt_lib,
    ):
        """adapt_memory.py and the shared lib (entity_io.py) land at the global path."""
        install_runner.run("install", platform="claude")

        file_assertions.assert_file_exists(claude_adapt_script)
        # The shipped script invokes itself from the stable path (no plugin root).
        assert "entity_io" in claude_adapt_script.read_text()
        # The shared lib must ship alongside so adapt_memory's import-walk resolves.
        file_assertions.assert_file_exists(claude_adapt_lib)

    def test_installed_adapt_script_is_runnable_from_stable_path(
        self,
        install_runner,
        temp_project_dir,
        sandbox_home,
        claude_adapt_script,
    ):
        """Run the GLOBALLY-installed adapt_memory.py: its `entity_io` import must
        resolve from ~/.claude/evolve-lite/lib/evolve-lite/ and it must write the
        mirrored entity into the project's .evolve store."""
        install_runner.run("install", platform="claude")

        native = temp_project_dir / "native_memory.md"
        native.write_text(
            "---\nname: prefer-ripgrep\ndescription: use ripgrep over grep\n"
            "metadata:\n  type: feedback\n---\nAlways reach for ripgrep (rg).\n"
        )
        evolve_dir = temp_project_dir / ".evolve"

        env = {
            **os.environ,
            "HOME": str(sandbox_home),
            "USERPROFILE": str(sandbox_home),
            "EVOLVE_DIR": str(evolve_dir),
        }
        env.pop("HOMEDRIVE", None)
        env.pop("HOMEPATH", None)
        result = subprocess.run(
            [sys.executable, str(claude_adapt_script), str(native), "--type", "feedback", "--trigger", "when searching files"],
            capture_output=True,
            text=True,
            cwd=str(temp_project_dir),
            env=env,
            check=False,
        )

        assert result.returncode == 0, f"adapt_memory.py failed: {result.stderr}"
        entity = evolve_dir / "entities" / "feedback" / "prefer-ripgrep.md"
        assert entity.is_file(), f"entity not written; stdout={result.stdout} stderr={result.stderr}"

    def test_uninstall_removes_adapt_script_and_lib(
        self,
        install_runner,
        file_assertions,
        claude_adapt_script,
        claude_adapt_lib,
    ):
        """Uninstall removes the global adapter script and the shipped lib."""
        install_runner.run("install", platform="claude")
        file_assertions.assert_file_exists(claude_adapt_script)
        file_assertions.assert_file_exists(claude_adapt_lib)

        install_runner.run("uninstall", platform="claude")

        file_assertions.assert_file_not_exists(claude_adapt_script)
        file_assertions.assert_file_not_exists(claude_adapt_lib)
        # The whole global evolve-lite dir (scripts + lib) is gone when emptied.
        file_assertions.assert_dir_not_exists(claude_adapt_script.parent)

    def test_dry_run_writes_no_adapt_artifacts(
        self,
        install_runner,
        claude_adapt_script,
        claude_adapt_lib,
    ):
        result = install_runner.run("install", platform="claude", dry_run=True)
        assert "DRY RUN" in result.stdout
        assert not claude_adapt_script.exists()
        assert not claude_adapt_lib.exists()


def _allow(settings_path):
    """The permissions.allow list from a settings.json (empty list if absent)."""
    if not settings_path.is_file():
        return []
    return json.loads(settings_path.read_text()).get("permissions", {}).get("allow", [])


@pytest.mark.platform_integrations
@pytest.mark.e2e
class TestClaudePermissionAllowlist:
    """Install pre-authorizes the evolve scripts + .evolve writes in project settings."""

    def test_install_merges_all_allow_rules(self, install_runner, claude_settings_file):
        install_runner.run("install", platform="claude")
        allow = _allow(claude_settings_file)
        for rule in EXPECTED_ALLOW_RULES:
            assert rule in allow, f"missing allow-rule {rule!r}; got {allow!r}"

    def test_reinstall_does_not_duplicate_rules(self, install_runner, claude_settings_file):
        install_runner.run("install", platform="claude")
        install_runner.run("install", platform="claude")
        allow = _allow(claude_settings_file)
        for rule in EXPECTED_ALLOW_RULES:
            assert allow.count(rule) == 1, f"rule {rule!r} duplicated: {allow!r}"

    def test_install_preserves_existing_rules_and_keys(self, install_runner, claude_settings_file):
        """A pre-existing unrelated allow-rule and other settings keys survive."""
        claude_settings_file.parent.mkdir(parents=True, exist_ok=True)
        claude_settings_file.write_text(
            json.dumps(
                {
                    "model": "opus",
                    "permissions": {
                        "allow": ["Bash(ls:*)"],
                        "deny": ["Bash(rm:*)"],
                    },
                },
                indent=2,
            )
            + "\n"
        )

        install_runner.run("install", platform="claude")

        data = json.loads(claude_settings_file.read_text())
        # Unrelated top-level key preserved.
        assert data["model"] == "opus"
        # Unrelated permissions sibling preserved.
        assert data["permissions"]["deny"] == ["Bash(rm:*)"]
        allow = data["permissions"]["allow"]
        # Pre-existing rule preserved and our rules merged in (no duplicates).
        assert "Bash(ls:*)" in allow
        for rule in EXPECTED_ALLOW_RULES:
            assert allow.count(rule) == 1

    def test_uninstall_removes_only_evolve_rules(self, install_runner, claude_settings_file):
        """Uninstall drops exactly the 5 evolve rules, leaving user rules + keys."""
        claude_settings_file.parent.mkdir(parents=True, exist_ok=True)
        claude_settings_file.write_text(
            json.dumps(
                {"model": "opus", "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm:*)"]}},
                indent=2,
            )
            + "\n"
        )
        install_runner.run("install", platform="claude")
        install_runner.run("uninstall", platform="claude")

        data = json.loads(claude_settings_file.read_text())
        assert data["model"] == "opus"
        assert data["permissions"]["deny"] == ["Bash(rm:*)"]
        assert data["permissions"]["allow"] == ["Bash(ls:*)"]
        for rule in EXPECTED_ALLOW_RULES:
            assert rule not in data["permissions"]["allow"]

    def test_uninstall_cleans_up_empties(self, install_runner, claude_settings_file, file_assertions):
        """When only evolve rules existed, uninstall removes the empty allow key,
        the settings file, and the .claude dir (if otherwise empty)."""
        install_runner.run("install", platform="claude")
        file_assertions.assert_file_exists(claude_settings_file)

        install_runner.run("uninstall", platform="claude")

        # Settings file removed (it reduced to {}), and .claude/ dir removed.
        file_assertions.assert_file_not_exists(claude_settings_file)
        file_assertions.assert_dir_not_exists(claude_settings_file.parent)

    def test_dry_run_writes_no_settings(self, install_runner, claude_settings_file):
        result = install_runner.run("install", platform="claude", dry_run=True)
        assert "DRY RUN" in result.stdout
        assert not claude_settings_file.exists()
