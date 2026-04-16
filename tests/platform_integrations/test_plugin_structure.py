"""Tests for plugin manifest integrity and hook script references."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"


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
    def test_hooks_json_is_valid_json(self):
        data = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        assert isinstance(data, dict)

    def test_hooks_json_has_hooks_key(self):
        data = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        assert "hooks" in data

    def test_known_lifecycle_events_present(self):
        data = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        hooks = data["hooks"]
        assert "UserPromptSubmit" in hooks
        assert "SessionStart" in hooks
        assert "Stop" in hooks

    def test_command_hook_scripts_exist(self):
        data = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        for event, groups in data["hooks"].items():
            for group in groups:
                for hook in group.get("hooks", []):
                    if hook.get("type") == "command":
                        cmd = hook["command"]
                        resolved = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(_PLUGIN_ROOT))
                        # Find the .py token — commands may have trailing flags
                        py_tokens = [t for t in resolved.split() if t.endswith(".py")]
                        assert py_tokens, f"No .py script found in hook command: {cmd}"
                        script_path = Path(py_tokens[0])
                        assert script_path.exists(), f"Hook script missing: {script_path} (event: {event})"


class TestSkillScripts:
    """Verify that every skill script referenced in the plugin exists on disk."""

    @pytest.mark.parametrize(
        "script_rel",
        [
            "skills/publish/scripts/publish.py",
            "skills/subscribe/scripts/subscribe.py",
            "skills/unsubscribe/scripts/unsubscribe.py",
            "skills/sync/scripts/sync.py",
            "skills/recall/scripts/retrieve_entities.py",
            "skills/learn/scripts/save_entities.py",
        ],
    )
    def test_script_exists(self, script_rel):
        script = _PLUGIN_ROOT / script_rel
        assert script.exists(), f"Script not found: {script}"


class TestLibModules:
    """Verify that the shared lib modules the scripts depend on exist."""

    @pytest.mark.parametrize(
        "module",
        [
            "lib/entity_io.py",
            "lib/config.py",
            "lib/audit.py",
        ],
    )
    def test_lib_module_exists(self, module):
        assert (_PLUGIN_ROOT / module).exists(), f"Lib module not found: {module}"
