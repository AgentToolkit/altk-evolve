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
    ("claude", CLAUDE_RETRIEVE_SCRIPT, "Entities for this task"),
    ("codex", CODEX_RETRIEVE_SCRIPT, "Evolve entities for this task"),
]

# The hook pipes this JSON to the script on stdin
HOOK_INPUT = json.dumps({"prompt": "How do I write clean code?"})


def run_retrieve(script_path, evolve_dir=None, stdin_data=None):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(script_path)],
        input=stdin_data or HOOK_INPUT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def evolve_dir(temp_project_dir, file_assertions):
    """An .evolve dir with one owned entity and one subscribed entity."""
    d = temp_project_dir / ".evolve"

    # Owned entity
    file_assertions.write_text(
        d / "entities" / "guideline" / "guideline.md",
        "---\ntype: guideline\n---\n\nKeep functions small.\n",
    )

    # Subscribed entity (lives under entities/subscribed/{name}/)
    file_assertions.write_text(
        d / "entities" / "subscribed" / "alice" / "guideline" / "alice-guideline.md",
        "---\ntype: guideline\nowner: alice\nvisibility: public\n---\n\nAlways write tests.\n",
    )

    return d


class TestRetrieve:
    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_exits_cleanly_with_no_output_when_no_entities_dir(self, temp_project_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=temp_project_dir / ".evolve")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_outputs_owned_entities(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "Keep functions small." in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_annotates_subscribed_entities_with_from_source(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir)
        assert "[from: alice]" in result.stdout
        assert "Always write tests." in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_owned_entities_not_annotated_with_from(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir)
        own_lines = [line for line in result.stdout.splitlines() if "Keep functions small." in line]
        assert own_lines
        assert not any("[from:" in line for line in own_lines)

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_output_includes_type_annotation(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir)
        assert "[guideline]" in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_handles_invalid_json_stdin_gracefully(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir, stdin_data="not valid json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_output_has_header(self, evolve_dir, retrieve_script, expected_header, platform_name):
        result = run_retrieve(retrieve_script, evolve_dir=evolve_dir)
        assert expected_header in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_entities_with_trigger_include_when_line(
        self, temp_project_dir, retrieve_script, expected_header, platform_name, file_assertions
    ):
        d = temp_project_dir / ".evolve"
        file_assertions.write_text(
            d / "entities" / "guideline" / "guideline.md",
            "---\ntype: guideline\ntrigger: when writing tests\n---\n\nAssert the important thing.\n",
        )
        result = run_retrieve(retrieve_script, evolve_dir=d)
        assert "when writing tests" in result.stdout

    @pytest.mark.parametrize(("platform_name", "retrieve_script", "expected_header"), SCRIPT_VARIANTS)
    def test_skips_symlinked_markdown_entities(self, temp_project_dir, retrieve_script, expected_header, platform_name):
        d = temp_project_dir / ".evolve"
        gdir = d / "entities" / "subscribed" / "alice" / "guideline"
        gdir.mkdir(parents=True)
        real_file = gdir / "real.md"
        real_file.write_text("---\ntype: guideline\n---\n\nReal content.\n")
        (gdir / "link.md").symlink_to(real_file)

        result = run_retrieve(retrieve_script, evolve_dir=d)

        assert result.returncode == 0
        assert result.stdout.count("Real content.") == 1
