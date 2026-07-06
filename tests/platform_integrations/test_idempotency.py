"""
Tests to ensure install.sh is idempotent - running it multiple times is safe.
"""

import json

import pytest


MANAGED_MARKER = "<!-- evolve-lite:managed -->"


@pytest.mark.platform_integrations
class TestBobIdempotency:
    """Test that Bob installation is idempotent."""

    def test_multiple_lite_installs(self, temp_project_dir, install_runner, file_assertions, bob_rules_file, bob_audit_script):
        """Running install twice for Bob lite mode should be safe.

        Lite writes the always-on instructions to Bob's GLOBAL rules file
        ``~/.bob/rules/00-evolve-lite.md`` and the recall-audit script to
        ``~/.bob/evolve-lite/audit_recall.py``; a second install must leave
        exactly one such file with identical content (no duplication) and must
        not create any AGENTS.md or per-project EVOLVE.md copy.
        """
        # First install
        install_runner.run("install", platform="bob", mode="lite")

        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_file_exists(bob_rules_file)
        first_content = bob_rules_file.read_text()
        # The rules file holds the full EVOLVE.md text.
        assert "self-directed memory" in first_content
        # The recall-audit script is installed at its global path, and the rules
        # file references that exact path.
        file_assertions.assert_file_exists(bob_audit_script)
        assert "Append a recall-audit row" in bob_audit_script.read_text()
        assert "~/.bob/evolve-lite/audit_recall.py" in first_content

        # Second install
        install_runner.run("install", platform="bob", mode="lite")

        # Assert: the rules file is identical after the second install.
        second_content = bob_rules_file.read_text()
        assert first_content == second_content, "rules/00-evolve-lite.md changed after second install"

        # Assert: exactly one rules file, no duplicates with other suffixes.
        rules_dir = bob_rules_file.parent
        evolve_rules = sorted(rules_dir.glob("*evolve-lite*.md"))
        assert evolve_rules == [bob_rules_file], f"Unexpected evolve rules files: {evolve_rules}"

        # Assert: lite never creates an AGENTS.md or a .bob/EVOLVE.md copy.
        file_assertions.assert_file_not_exists(temp_project_dir / "AGENTS.md")
        file_assertions.assert_file_not_exists(bob_dir / "AGENTS.md")
        file_assertions.assert_file_not_exists(bob_dir / "EVOLVE.md")

        # Assert: Lite does not write custom_modes.yaml
        file_assertions.assert_file_not_exists(bob_dir / "custom_modes.yaml")

        # Assert: All skills still exist
        file_assertions.assert_all_bob_skills_installed(bob_dir)

    def test_multiple_full_installs(self, temp_project_dir, install_runner, file_assertions):
        """Running install twice for Bob full mode should be safe."""
        # First install
        install_runner.run("install", platform="bob", mode="full")

        # Capture state after first install
        bob_dir = temp_project_dir / ".bob"
        mcp_file = bob_dir / "mcp.json"
        first_data = json.loads(mcp_file.read_text())

        # Second install
        install_runner.run("install", platform="bob", mode="full")

        # Assert: MCP config is identical
        second_data = json.loads(mcp_file.read_text())
        assert first_data == second_data, "MCP config changed after second install"

        # Assert: Only one evolve server entry
        assert "evolve" in second_data["mcpServers"]
        assert len([k for k in second_data["mcpServers"].keys() if k == "evolve"]) == 1

    def test_install_after_partial_uninstall(self, temp_project_dir, install_runner, file_assertions, bob_rules_file):
        """Installing after manually deleting some components should restore them."""
        # Initial install
        install_runner.run("install", platform="bob")

        bob_dir = temp_project_dir / ".bob"

        # Manually delete one skill
        import shutil

        shutil.rmtree(bob_dir / "skills" / "evolve-lite-save")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: All skills are restored
        file_assertions.assert_all_bob_skills_installed(bob_dir)
        # Lite wires always-on instructions via the GLOBAL rules file.
        file_assertions.assert_file_exists(bob_rules_file)


@pytest.mark.platform_integrations
class TestBobLegacyMigration:
    """Upgrade path from the pre-rename `evolve-lite:<name>` colon-form layout."""

    def _seed_legacy_artifacts(self, bob_dir):
        """Drop a stale colon-form skill + command into .bob/, the way an old install left it."""
        legacy_skill = bob_dir / "skills" / "evolve-lite:learn"
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SKILL.md").write_text("legacy colon-form skill\n")
        legacy_cmd = bob_dir / "commands" / "evolve-lite:learn.md"
        legacy_cmd.parent.mkdir(parents=True, exist_ok=True)
        legacy_cmd.write_text("legacy colon-form command\n")
        return legacy_skill, legacy_cmd

    def test_install_purges_legacy_colon_form(self, temp_project_dir, install_runner, file_assertions):
        """Re-running install over a pre-rename layout wipes the legacy artifacts.

        Reproduces visahak's PR #235 finding: without the purge, `.bob/skills/`
        ends up with both `evolve-lite:learn` and `evolve-lite-learn` after
        upgrade.
        """
        bob_dir = temp_project_dir / ".bob"
        legacy_skill, legacy_cmd = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("install", platform="bob", mode="lite")

        # Legacy artifacts gone
        assert not legacy_skill.exists(), "legacy colon-form skill survived install"
        assert not legacy_cmd.exists(), "legacy colon-form command survived install"
        # Current dash-form layout in place
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-lite-save")
        file_assertions.assert_file_exists(bob_dir / "commands" / "evolve-lite-save.md")

    def test_uninstall_purges_legacy_colon_form(self, temp_project_dir, install_runner, file_assertions):
        """Uninstall removes legacy colon-form stragglers alongside the dash-form."""
        install_runner.run("install", platform="bob", mode="lite")
        bob_dir = temp_project_dir / ".bob"

        # Inject legacy artifacts post-install — simulates an upgrade gap where
        # a user moved through several versions and accumulated both forms.
        legacy_skill, legacy_cmd = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("uninstall", platform="bob")

        assert not legacy_skill.exists(), "uninstall left legacy colon-form skill behind"
        assert not legacy_cmd.exists(), "uninstall left legacy colon-form command behind"
        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-save")
        file_assertions.assert_file_not_exists(bob_dir / "commands" / "evolve-lite-save.md")

    def test_uninstall_removes_rules_file_and_preserves_user_rules(
        self, temp_project_dir, install_runner, file_assertions, bob_rules_file, bob_audit_script
    ):
        """Lite uninstall removes the global rules file + audit script, leaving unrelated rules intact."""
        install_runner.run("install", platform="bob", mode="lite")
        file_assertions.assert_file_exists(bob_rules_file)
        file_assertions.assert_file_exists(bob_audit_script)

        # A pre-existing unrelated rules file the installer doesn't own.
        user_rule = bob_rules_file.parent / "99-user.md"
        original = "# My personal rules\n\nAlways prefer tabs.\n"
        user_rule.write_text(original)

        install_runner.run("uninstall", platform="bob")

        file_assertions.assert_file_not_exists(bob_rules_file)
        file_assertions.assert_file_not_exists(bob_audit_script)
        # The now-empty ~/.bob/evolve-lite/ dir is tidied up.
        file_assertions.assert_dir_not_exists(bob_audit_script.parent)
        file_assertions.assert_file_unchanged(user_rule, original)

    def test_uninstall_removes_namespaced_shared_lib(self, temp_project_dir, install_runner, file_assertions):
        """Uninstall must remove the namespaced shared lib at .bob/lib/evolve-lite/.

        After the lib/ namespacing rename, the lib is no longer an evolve-prefixed
        top-level dir, so the generic purge loop misses it. Regression guard for
        gaodan-fang's PR #258 finding (uninstall left .bob/lib/evolve-lite/ behind).
        """
        install_runner.run("install", platform="bob", mode="lite")
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "lib" / "evolve-lite")

        install_runner.run("uninstall", platform="bob")

        file_assertions.assert_dir_not_exists(bob_dir / "lib" / "evolve-lite")

    def test_install_preserves_user_content_during_legacy_purge(self, temp_project_dir, install_runner, bob_fixtures, file_assertions):
        """The legacy purge MUST NOT clobber non-evolve user skills/commands."""
        bob_dir = temp_project_dir / ".bob"
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        custom_command = bob_fixtures.create_existing_command(temp_project_dir)
        legacy_skill, _ = self._seed_legacy_artifacts(bob_dir)

        install_runner.run("install", platform="bob", mode="lite")

        # Legacy purged, user content intact.
        assert not legacy_skill.exists()
        file_assertions.assert_dir_exists(custom_skill)
        file_assertions.assert_file_exists(custom_command)


@pytest.mark.platform_integrations
class TestCodexIdempotency:
    """Test that Codex installation is idempotent."""

    def test_multiple_installs(
        self, temp_project_dir, install_runner, file_assertions, codex_agents_file, codex_evolve_md, codex_audit_script
    ):
        """Running install twice for Codex should be safe.

        Codex now drops a COPY of EVOLVE.md on disk and injects a SINGLE
        greppable pointer line (carrying ``<!-- evolve-lite:managed -->``) into
        the (sandboxed) ~/.codex/AGENTS.md instead of inlining the body. A
        second install must not duplicate the marketplace entry or the pointer
        line.
        """
        install_runner.run("install", platform="codex")

        marketplace_file = temp_project_dir / ".agents" / "plugins" / "marketplace.json"
        first_marketplace = json.loads(marketplace_file.read_text())
        first_agents = codex_agents_file.read_text()

        # The recall-audit script and the EVOLVE.md copy live together on disk;
        # the pointer line in AGENTS.md references the EVOLVE.md path.
        file_assertions.assert_file_exists(codex_evolve_md)
        file_assertions.assert_file_exists(codex_audit_script)
        assert "Append a recall-audit row" in codex_audit_script.read_text()
        assert "~/.codex/evolve-lite/EVOLVE.md" in first_agents

        install_runner.run("install", platform="codex")

        second_marketplace = json.loads(marketplace_file.read_text())
        second_agents = codex_agents_file.read_text()

        assert first_marketplace == second_marketplace, "marketplace.json changed after second install"
        assert first_agents == second_agents, "~/.codex/AGENTS.md changed after second install"

        evolve_plugins = [entry for entry in second_marketplace["plugins"] if entry["name"] == "evolve-lite"]
        assert len(evolve_plugins) == 1, "Duplicate evolve-lite marketplace entries found"

        # Exactly one managed pointer line in the always-on instructions file.
        marker_lines = [ln for ln in second_agents.splitlines() if MANAGED_MARKER in ln]
        assert len(marker_lines) == 1, f"Expected exactly one managed line, got {marker_lines!r}"
        # The EVOLVE.md copy and audit script are still present after reinstall.
        file_assertions.assert_file_exists(codex_evolve_md)
        file_assertions.assert_file_exists(codex_audit_script)

    def test_install_after_partial_uninstall(self, temp_project_dir, install_runner, file_assertions):
        """Installing after deleting part of the Codex plugin should restore it."""
        install_runner.run("install", platform="codex")

        plugin_dir = temp_project_dir / "plugins" / "evolve-lite"

        import shutil

        shutil.rmtree(plugin_dir / "skills" / "evolve-lite" / "save")

        install_runner.run("install", platform="codex")

        file_assertions.assert_dir_exists(plugin_dir / "skills" / "evolve-lite" / "save")
        file_assertions.assert_file_exists(plugin_dir / "skills" / "evolve-lite" / "save" / "SKILL.md")
        file_assertions.assert_file_exists(plugin_dir / "lib" / "evolve-lite" / "entity_io.py")

    def test_install_appends_pointer_preserving_user_prose(self, temp_project_dir, install_runner, file_assertions, codex_agents_file):
        """Injecting the pointer line must preserve a pre-existing, unrelated AGENTS.md.

        Codex now injects a SINGLE managed pointer line (carrying
        ``<!-- evolve-lite:managed -->``) via FileOps.inject_marker_line. When
        AGENTS.md already has user content but no managed line, the pointer is
        APPENDED on its own line — separated from the existing content by a
        blank line — and the user's prose is preserved verbatim. Re-running the
        install REPLACES that one line in place rather than duplicating it.
        """
        # The sandboxed ~/.codex/AGENTS.md, pre-seeded with unrelated user prose.
        codex_agents_file.parent.mkdir(parents=True, exist_ok=True)
        user_prose = "# My agent instructions\n\nAlways prefer ripgrep over grep, and never edit generated files by hand.\n"
        codex_agents_file.write_text(user_prose)

        install_runner.run("install", platform="codex")

        content = codex_agents_file.read_text()
        # The user's original prose is preserved verbatim.
        assert user_prose.rstrip() in content
        # Exactly one managed pointer line was appended, separated by a blank line.
        marker_lines = [ln for ln in content.splitlines() if MANAGED_MARKER in ln]
        assert len(marker_lines) == 1, f"Expected exactly one managed line, got {marker_lines!r}"
        assert content.startswith(user_prose.rstrip() + "\n\n")

        # A second install replaces the line in place — still exactly one.
        install_runner.run("install", platform="codex")
        content2 = codex_agents_file.read_text()
        marker_lines2 = [ln for ln in content2.splitlines() if MANAGED_MARKER in ln]
        assert len(marker_lines2) == 1, f"Expected exactly one managed line after reinstall, got {marker_lines2!r}"
        assert user_prose.rstrip() in content2


@pytest.mark.platform_integrations
class TestUninstallInstallCycle:
    """Test that uninstall followed by install works correctly."""

    def test_bob_uninstall_install_cycle(self, temp_project_dir, install_runner, bob_fixtures, file_assertions, bob_rules_file):
        """Uninstalling and reinstalling Bob should work correctly."""
        # Create user content
        bob_fixtures.create_existing_skill(temp_project_dir)
        bob_fixtures.create_existing_custom_modes(temp_project_dir)

        # Install
        install_runner.run("install", platform="bob")

        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-lite-save")

        # Uninstall
        install_runner.run("uninstall", platform="bob")

        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-save")
        file_assertions.assert_dir_not_exists(bob_dir / "skills" / "evolve-lite-provenance")

        # Reinstall
        install_runner.run("install", platform="bob")

        # Assert: Evolve content is back. Lite wires always-on instructions via
        # the GLOBAL rules file, not via custom_modes.yaml or any AGENTS.md.
        file_assertions.assert_all_bob_skills_installed(bob_dir)
        file_assertions.assert_file_exists(bob_rules_file)
        file_assertions.assert_file_not_exists(temp_project_dir / "AGENTS.md")
        file_assertions.assert_file_not_exists(bob_dir / "EVOLVE.md")

        # Assert: User content still intact — the user's custom_modes.yaml was never
        # touched by the lite install, so their mode survives the full cycle.
        file_assertions.assert_dir_exists(bob_dir / "skills" / "my-custom-skill")
        custom_modes = (bob_dir / "custom_modes.yaml").read_text()
        assert "slug: my-mode" in custom_modes

    def test_codex_uninstall_install_cycle(
        self,
        temp_project_dir,
        install_runner,
        codex_fixtures,
        file_assertions,
        codex_agents_file,
        codex_evolve_md,
        codex_audit_script,
    ):
        """Uninstalling and reinstalling Codex should work correctly.

        Codex now drops a COPY of EVOLVE.md on disk and injects a SINGLE managed
        pointer line into the (sandboxed) ~/.codex/AGENTS.md instead of
        registering hooks. The user's hooks.json is never touched, so it must
        survive the cycle unchanged.
        """
        custom_plugin = codex_fixtures.create_existing_plugin(temp_project_dir)
        marketplace_file = codex_fixtures.create_existing_marketplace(temp_project_dir)
        hooks_file = codex_fixtures.create_existing_hooks(temp_project_dir)

        plugin_json = custom_plugin / ".codex-plugin" / "plugin.json"
        original_plugin_content = plugin_json.read_text()
        original_hooks_content = hooks_file.read_text()

        install_runner.run("install", platform="codex")

        evolve_plugin_dir = temp_project_dir / "plugins" / "evolve-lite"
        file_assertions.assert_dir_exists(evolve_plugin_dir)
        # Install injected exactly one managed pointer line into the always-on instructions.
        marker_lines = [ln for ln in codex_agents_file.read_text().splitlines() if MANAGED_MARKER in ln]
        assert len(marker_lines) == 1, f"Expected exactly one managed line, got {marker_lines!r}"
        # Install dropped the EVOLVE.md copy and the recall-audit script at their global paths.
        file_assertions.assert_file_exists(codex_evolve_md)
        file_assertions.assert_file_exists(codex_audit_script)
        # The user's hooks were left completely untouched.
        file_assertions.assert_file_unchanged(hooks_file, original_hooks_content)

        install_runner.run("uninstall", platform="codex")

        file_assertions.assert_dir_not_exists(evolve_plugin_dir)
        current_marketplace = json.loads(marketplace_file.read_text())
        assert all(entry["name"] != "evolve-lite" for entry in current_marketplace["plugins"])

        # The managed pointer line is gone from AGENTS.md after uninstall.
        assert [ln for ln in codex_agents_file.read_text().splitlines() if MANAGED_MARKER in ln] == []
        # The EVOLVE.md copy, audit script, and now-empty dir are removed.
        file_assertions.assert_file_not_exists(codex_evolve_md)
        file_assertions.assert_file_not_exists(codex_audit_script)
        file_assertions.assert_dir_not_exists(codex_evolve_md.parent)
        # The user's hooks are still untouched.
        file_assertions.assert_file_unchanged(hooks_file, original_hooks_content)

        install_runner.run("install", platform="codex")

        file_assertions.assert_dir_exists(evolve_plugin_dir)
        file_assertions.assert_file_unchanged(plugin_json, original_plugin_content)

        reinstalled_marketplace = json.loads(marketplace_file.read_text())
        assert any(entry["name"] == "my-codex-plugin" for entry in reinstalled_marketplace["plugins"])
        assert any(entry["name"] == "evolve-lite" for entry in reinstalled_marketplace["plugins"])

        # Reinstall re-injects exactly one managed pointer line and still leaves user hooks alone.
        reinstalled_markers = [ln for ln in codex_agents_file.read_text().splitlines() if MANAGED_MARKER in ln]
        assert len(reinstalled_markers) == 1, f"Expected exactly one managed line, got {reinstalled_markers!r}"
        file_assertions.assert_file_exists(codex_evolve_md)
        file_assertions.assert_file_unchanged(hooks_file, original_hooks_content)
