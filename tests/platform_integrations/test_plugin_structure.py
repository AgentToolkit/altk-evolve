"""Tests for plugin manifest integrity and hook script references."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"
_CODEX_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/codex/plugins/evolve-lite"
_CLAW_CODE_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claw-code/plugins/evolve-lite"


class TestPluginManifest:
    def test_plugin_json_is_valid_json(self):
        data = json.loads((_PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        assert isinstance(data, dict)

    def test_plugin_json_has_required_fields(self):
        data = json.loads((_PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        for field in ("name", "version", "description"):
            assert field in data, f"plugin.json missing required field: {field}"

    def test_plugin_json_skills_path_exists(self):
        data = json.loads((_PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
        skills_path = (_PLUGIN_ROOT / data["skills"]).resolve()
        assert skills_path.is_dir(), f"skills path does not exist: {skills_path}"


class TestHooksManifest:
    """The Claude plugin is fully hookless under the native-memory + CLAUDE.md
    `@import` redesign. Recall is native and save is native, so the plugin must
    register NO auto-firing hooks — otherwise recall/save fire twice. The skills
    themselves stay invokable (see TestSkillScripts); only the hook WIRING is gone.
    """

    def test_no_hooks_json_shipped(self):
        # No hooks/hooks.json under the rendered Claude plugin: the plugin
        # registers no auto-firing lifecycle hooks at all.
        assert not (_PLUGIN_ROOT / "hooks" / "hooks.json").exists()

    def test_no_hooks_directory(self):
        # The render wipes and rewrites the plugin root from plugin-source/;
        # with the source hooks.json removed, no hooks/ dir should remain.
        assert not (_PLUGIN_ROOT / "hooks").exists()


class TestSkillScripts:
    """Verify that every skill script referenced in the plugin exists on disk."""

    @pytest.mark.parametrize(
        "script_rel",
        [
            "skills/evolve-lite/publish/scripts/publish.py",
            "skills/evolve-lite/subscribe/scripts/subscribe.py",
            "skills/evolve-lite/unsubscribe/scripts/unsubscribe.py",
            "skills/evolve-lite/sync/scripts/sync.py",
            "skills/evolve-lite/provenance/scripts/log_influence.py",
            "skills/evolve-lite/adapt-memory/scripts/adapt_memory.py",
            "skills/evolve-lite/doctor/scripts/doctor.py",
        ],
    )
    def test_script_exists(self, script_rel):
        script = _PLUGIN_ROOT / script_rel
        assert script.exists(), f"Script not found: {script}"

    def test_codex_save_trajectory_skill_documents_helper_invocation(self):
        skill = _CODEX_PLUGIN_ROOT / "skills/evolve-lite/save-trajectory/SKILL.md"
        content = skill.read_text()
        assert "plugins/evolve-lite/skills/evolve-lite/save-trajectory/scripts/save_trajectory.py" in content


class TestRecallLearnExcludedFromClaudeCodexBob:
    """EVOLVE.md's injected recall + direct entity-save instructions drive the
    identical workflow on claude, codex, and bob, so the recall/learn skills
    are redundant double-delivery and excluded from those plugins. Only
    claw-code keeps them — its PreToolUse hook consumes recall's
    retrieve_entities.py."""

    @pytest.mark.parametrize("skill", ["recall", "learn"])
    def test_claude_plugin_lacks_skill(self, skill):
        assert not (_PLUGIN_ROOT / "skills/evolve-lite" / skill).exists(), (
            f"Claude plugin must not ship the `{skill}` skill — native memory owns it"
        )

    @pytest.mark.parametrize("skill", ["recall", "learn"])
    def test_codex_plugin_lacks_skill(self, skill):
        assert not (_CODEX_PLUGIN_ROOT / "skills/evolve-lite" / skill).exists(), (
            f"codex must not ship the `{skill}` skill — EVOLVE.md drives the workflow"
        )

    @pytest.mark.parametrize("skill", ["recall", "learn"])
    def test_claw_code_plugin_still_has_skill(self, skill):
        assert (_CLAW_CODE_PLUGIN_ROOT / "skills/evolve-lite" / skill / "SKILL.md").is_file(), (
            f"claw-code must still ship the `{skill}` skill — its PreToolUse hook consumes it"
        )


class TestLibModules:
    """Verify that the shared lib modules the scripts depend on exist."""

    @pytest.mark.parametrize(
        "module",
        [
            "lib/evolve-lite/entity_io.py",
            "lib/evolve-lite/config.py",
            "lib/evolve-lite/audit.py",
        ],
    )
    def test_lib_module_exists(self, module):
        assert (_PLUGIN_ROOT / module).exists(), f"Lib module not found: {module}"
