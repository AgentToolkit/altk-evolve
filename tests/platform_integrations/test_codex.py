"""
Tests for the Codex platform integration installer behavior.

The Codex redesign no longer registers UserPromptSubmit/SessionStart hooks, and
no longer INLINES the full EVOLVE.md into ``~/.codex/AGENTS.md``. Instead the
installer:
  * copies the plugin tree + upserts the marketplace entry, and
  * drops a COPY of EVOLVE.md at the GLOBAL path
    ``~/.codex/evolve-lite/EVOLVE.md``, and
  * injects a SINGLE greppable pointer line (carrying the
    ``<!-- evolve-lite:managed -->`` marker) into the GLOBAL (sandboxed)
    ``~/.codex/AGENTS.md`` telling the agent to read that file, and
  * drops the self-contained recall-audit script at the GLOBAL path
    ``~/.codex/evolve-lite/audit_recall.py`` referenced by that file.
"""

import json

import pytest


EVOLVE_PLUGIN = "evolve-lite"
MANAGED_MARKER = "<!-- evolve-lite:managed -->"
EVOLVE_MD_REF = "~/.codex/evolve-lite/EVOLVE.md"
AUDIT_PATH_REF = "~/.codex/evolve-lite/audit_recall.py"
# A distinctive sentence from the body of EVOLVE.md that must live in the copied
# file but must NOT be inlined into AGENTS.md anymore.
EVOLVE_BODY_SENTENCE = "You have a persistent, file-based memory for the current project"


def _marketplace_has_evolve_plugin(path):
    data = json.loads(path.read_text())
    return any(entry.get("name") == EVOLVE_PLUGIN for entry in data.get("plugins", []))


def _marker_lines(text):
    """Return the list of lines in `text` that carry the managed marker."""
    return [ln for ln in text.splitlines() if MANAGED_MARKER in ln]


@pytest.mark.platform_integrations
@pytest.mark.e2e
class TestCodexInstall:
    """Test the Codex install flow."""

    def test_install_creates_expected_files(
        self,
        temp_project_dir,
        install_runner,
        file_assertions,
        codex_agents_file,
        codex_evolve_md,
        codex_audit_script,
    ):
        """Installing Codex creates the plugin tree, marketplace entry, AGENTS.md pointer, EVOLVE.md copy, and audit script."""
        install_runner.run("install", platform="codex")

        plugin_dir = temp_project_dir / "plugins" / EVOLVE_PLUGIN
        file_assertions.assert_dir_exists(plugin_dir)
        file_assertions.assert_file_exists(plugin_dir / ".codex-plugin" / "plugin.json")
        file_assertions.assert_file_exists(plugin_dir / "README.md")
        # recall/learn are excluded from codex — EVOLVE.md's injected
        # first-action recall + direct entity-save instructions drive the
        # identical workflow, so the skills would be redundant double-delivery.
        file_assertions.assert_dir_not_exists(plugin_dir / "skills" / "evolve-lite" / "learn")
        file_assertions.assert_dir_not_exists(plugin_dir / "skills" / "evolve-lite" / "recall")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "publish")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "provenance")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "save-trajectory")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "subscribe")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "unsubscribe")
        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "sync")
        file_assertions.assert_file_not_exists(plugin_dir / "skills" / "evolve-lite" / "learn" / "scripts" / "save_entities.py")
        file_assertions.assert_file_not_exists(plugin_dir / "skills" / "evolve-lite" / "recall" / "scripts" / "retrieve_entities.py")
        file_assertions.assert_file_exists(plugin_dir / "lib" / "evolve-lite" / "entity_io.py")
        # The recall-audit script ships in the plugin tree too, alongside the
        # shared lib (lib/evolve-lite/).
        file_assertions.assert_file_exists(plugin_dir / "lib" / "evolve-lite" / "audit_recall.py")

        marketplace_path = temp_project_dir / ".agents" / "plugins" / "marketplace.json"
        file_assertions.assert_valid_json(marketplace_path)
        assert _marketplace_has_evolve_plugin(marketplace_path), "Evolve plugin entry missing from marketplace.json"

        # A SINGLE greppable pointer line is injected into the GLOBAL ~/.codex/AGENTS.md.
        file_assertions.assert_file_exists(codex_agents_file)
        agents_text = codex_agents_file.read_text()
        marker_lines = _marker_lines(agents_text)
        assert len(marker_lines) == 1, f"Expected exactly one managed line, got {marker_lines!r}"
        pointer_line = marker_lines[0]
        # The pointer references the on-disk EVOLVE.md copy.
        assert EVOLVE_MD_REF in pointer_line
        # AGENTS.md must NOT inline the full EVOLVE.md body anymore.
        assert EVOLVE_BODY_SENTENCE not in agents_text
        # The audit-script path is no longer inlined into AGENTS.md (it lives in EVOLVE.md).
        assert AUDIT_PATH_REF not in agents_text

        # A COPY of EVOLVE.md is dropped on disk and DOES contain the full body.
        file_assertions.assert_file_exists(codex_evolve_md)
        evolve_md_text = codex_evolve_md.read_text()
        assert EVOLVE_BODY_SENTENCE in evolve_md_text
        # EVOLVE.md is what tells the model to run the recall-audit script.
        assert AUDIT_PATH_REF in evolve_md_text

        # The recall-audit script is installed alongside EVOLVE.md and is self-contained.
        file_assertions.assert_file_exists(codex_audit_script)
        assert codex_audit_script.parent == codex_evolve_md.parent
        assert "Append a recall-audit row" in codex_audit_script.read_text()

    def test_codex_dry_run_does_not_write_files(
        self, temp_project_dir, install_runner, codex_agents_file, codex_evolve_md, codex_audit_script
    ):
        """Dry-run should report actions without writing files."""
        result = install_runner.run("install", platform="codex", dry_run=True)

        assert "DRY RUN" in result.stdout
        assert not (temp_project_dir / "plugins" / EVOLVE_PLUGIN).exists()
        assert not (temp_project_dir / ".agents" / "plugins" / "marketplace.json").exists()
        assert not codex_agents_file.exists()
        assert not codex_evolve_md.exists()
        assert not codex_audit_script.exists()

    def test_uninstall_removes_pointer_and_files(
        self,
        temp_project_dir,
        install_runner,
        file_assertions,
        codex_agents_file,
        codex_evolve_md,
        codex_audit_script,
    ):
        """Uninstall removes the AGENTS.md pointer line, the EVOLVE.md copy, and the audit script (and the empty dir)."""
        install_runner.run("install", platform="codex")
        file_assertions.assert_file_exists(codex_evolve_md)
        file_assertions.assert_file_exists(codex_audit_script)
        assert len(_marker_lines(codex_agents_file.read_text())) == 1

        install_runner.run("uninstall", platform="codex")

        assert _marker_lines(codex_agents_file.read_text()) == []
        file_assertions.assert_file_not_exists(codex_evolve_md)
        file_assertions.assert_file_not_exists(codex_audit_script)
        file_assertions.assert_dir_not_exists(codex_evolve_md.parent)

    def test_status_reports_codex_installation(self, temp_project_dir, install_runner):
        """Status should show the Codex installation state under the new contract."""
        install_runner.run("install", platform="codex")
        result = install_runner.run("status")

        assert "Codex:" in result.stdout
        assert "plugins/evolve-lite" in result.stdout
        assert "marketplace.json entry" in result.stdout
        assert "~/.codex/AGENTS.md pointer" in result.stdout
        assert "EVOLVE.md" in result.stdout
        assert "audit_recall.py" in result.stdout
