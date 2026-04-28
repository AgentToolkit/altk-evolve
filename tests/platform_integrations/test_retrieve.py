"""Tests for skills/recall/scripts/retrieve_entities.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_REPO_ROOT = Path(__file__).parent.parent.parent
CLAUDE_RETRIEVE_SCRIPT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"
CODEX_RETRIEVE_SCRIPT = _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite/skills/recall/scripts/retrieve_entities.py"
SCRIPT_VARIANTS = [
    ("claude", CLAUDE_RETRIEVE_SCRIPT, "Evolve entity manifest for this task"),
    ("codex", CODEX_RETRIEVE_SCRIPT, "Evolve entity manifest for this task"),
]

# The hook pipes this JSON to the script on stdin
HOOK_INPUT = json.dumps({"prompt": "How do I write clean code?"})


def run_retrieve(script_path, project_dir, evolve_dir=None, stdin_data=None):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script_path)],
        input=stdin_data or HOOK_INPUT,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        env=env,
        check=False,
    )


def parse_manifest_lines(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]


@pytest.fixture
def evolve_dir(temp_project_dir, file_assertions):
    """An .evolve dir with one owned entity and one subscribed entity."""
    d = temp_project_dir / ".evolve"

    # Owned entity
    file_assertions.write_text(
        d / "entities" / "guideline" / "guideline.md",
        "---\ntype: guideline\ntrigger: when refactoring\n---\n\nKeep functions small.\n",
    )

    # Subscribed entity (lives under entities/subscribed/{name}/)
    file_assertions.write_text(
        d / "entities" / "subscribed" / "alice" / "guideline" / "alice-guideline.md",
        "---\ntype: guideline\ntrigger: when adding coverage\nowner: alice\nvisibility: public\n---\n\nAlways write tests.\n",
    )

    return d


class TestRetrieve:
    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_exits_cleanly_with_no_output_when_no_entities_dir(self, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=temp_project_dir / ".evolve")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_outputs_owned_entities(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir)
        assert result.returncode == 0
        entries = parse_manifest_lines(result.stdout)
        own_entry = {"path": ".evolve/entities/guideline/guideline.md", "type": "guideline", "trigger": "when refactoring"}
        assert own_entry in entries

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_includes_subscribed_entities(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir)
        entries = parse_manifest_lines(result.stdout)
        sub_entry = {
            "path": ".evolve/entities/subscribed/alice/guideline/alice-guideline.md",
            "type": "guideline",
            "trigger": "when adding coverage",
        }
        assert sub_entry in entries

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_manifest_entries_contain_only_path_type_trigger(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir)
        for entry in parse_manifest_lines(result.stdout):
            assert set(entry.keys()) == {"path", "type", "trigger"}

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_does_not_emit_full_bodies(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir)
        assert "Keep functions small." not in result.stdout
        assert "Always write tests." not in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_handles_invalid_json_stdin_gracefully(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir, stdin_data="not valid json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_output_has_header(self, evolve_dir, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=evolve_dir)
        assert expected_header in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_public_entities_included_in_recall(self, temp_project_dir, retrieve_script, expected_header, platform_name, file_assertions):
        d = temp_project_dir / ".evolve"
        file_assertions.write_text(
            d / "public" / "guideline" / "pub.md",
            "---\ntype: guideline\ntrigger: when choosing data structures\nvisibility: public\n---\n\nPrefer immutable data structures.\n",
        )
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=d)
        assert result.returncode == 0
        entries = parse_manifest_lines(result.stdout)
        pub_entry = {"path": ".evolve/public/guideline/pub.md", "type": "guideline", "trigger": "when choosing data structures"}
        assert pub_entry in entries
        assert "Prefer immutable data structures." not in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_entities_with_trigger_include_trigger_in_manifest(
        self, temp_project_dir, retrieve_script, expected_header, platform_name, file_assertions
    ):
        d = temp_project_dir / ".evolve"
        file_assertions.write_text(
            d / "entities" / "guideline" / "guideline.md",
            "---\ntype: guideline\ntrigger: when writing tests\n---\n\nAssert the important thing.\n",
        )
        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=d)
        assert "when writing tests" in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_skips_symlinked_markdown_entities(self, temp_project_dir, retrieve_script, expected_header, platform_name):
        d = temp_project_dir / ".evolve"
        gdir = d / "entities" / "subscribed" / "alice" / "guideline"
        gdir.mkdir(parents=True)
        real_file = gdir / "real.md"
        real_file.write_text("---\ntype: guideline\ntrigger: when testing\n---\n\nReal content.\n")
        (gdir / "link.md").symlink_to(real_file)

        result = run_retrieve(retrieve_script, temp_project_dir, evolve_dir=d)

        assert result.returncode == 0
        entries = parse_manifest_lines(result.stdout)
        assert len(entries) == 1
        assert entries[0]["trigger"] == "when testing"
