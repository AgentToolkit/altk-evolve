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

import pytest


# PATH that contains no claude binary — forces the "CLI not found" fallback path.
_NO_CLAUDE_PATH = "/usr/bin:/bin"

# The single native CLAUDE.md import pointer line (its own uninstall handle).
IMPORT_LINE = "@.evolve/EVOLVE.md"
# A distinctive sentence from the thin EVOLVE.md body that must live in the copy.
EVOLVE_BODY_SENTENCE = "You already have native, self-directed memory"
# A distinctive string from the recall-audit script.
AUDIT_SCRIPT_SENTENCE = "Append a recall-audit row"


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
