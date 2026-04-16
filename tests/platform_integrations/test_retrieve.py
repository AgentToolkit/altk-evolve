"""Tests for skills/recall/scripts/retrieve_entities.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_PLUGIN_ROOT = (
    Path(__file__).parent.parent.parent
    / "platform-integrations/claude/plugins/evolve-lite"
)
RETRIEVE_SCRIPT = _PLUGIN_ROOT / "skills/recall/scripts/retrieve_entities.py"

# The hook pipes this JSON to the script on stdin
HOOK_INPUT = json.dumps({"prompt": "How do I write clean code?"})


def run_retrieve(evolve_dir=None, stdin_data=None):
    env = {**os.environ}
    if evolve_dir:
        env["EVOLVE_DIR"] = str(evolve_dir)
    return subprocess.run(
        [sys.executable, str(RETRIEVE_SCRIPT)],
        input=stdin_data or HOOK_INPUT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def evolve_dir(tmp_path):
    """An .evolve dir with one owned entity and one subscribed entity."""
    d = tmp_path / ".evolve"

    # Owned entity
    own_dir = d / "entities" / "guideline"
    own_dir.mkdir(parents=True)
    (own_dir / "tip.md").write_text("---\ntype: guideline\n---\n\nKeep functions small.\n")

    # Subscribed entity (lives under entities/subscribed/{name}/)
    sub_dir = d / "entities" / "subscribed" / "alice" / "guideline"
    sub_dir.mkdir(parents=True)
    (sub_dir / "alice-tip.md").write_text(
        "---\ntype: guideline\nowner: alice\nvisibility: public\n---\n\nAlways write tests.\n"
    )

    return d


class TestRetrieve:
    def test_exits_cleanly_with_no_output_when_no_entities_dir(self, tmp_path):
        result = run_retrieve(evolve_dir=tmp_path / ".evolve")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_outputs_owned_entities(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir)
        assert result.returncode == 0
        assert "Keep functions small." in result.stdout

    def test_annotates_subscribed_entities_with_from_source(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir)
        assert "[from: alice]" in result.stdout
        assert "Always write tests." in result.stdout

    def test_owned_entities_not_annotated_with_from(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir)
        own_lines = [l for l in result.stdout.splitlines() if "Keep functions small." in l]
        assert own_lines
        assert not any("[from:" in l for l in own_lines)

    def test_output_includes_type_annotation(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir)
        assert "[guideline]" in result.stdout

    def test_handles_invalid_json_stdin_gracefully(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir, stdin_data="not valid json")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_output_has_header(self, evolve_dir):
        result = run_retrieve(evolve_dir=evolve_dir)
        assert "Entities for this task" in result.stdout

    def test_entities_with_trigger_include_when_line(self, tmp_path):
        d = tmp_path / ".evolve"
        gdir = d / "entities" / "guideline"
        gdir.mkdir(parents=True)
        (gdir / "tip.md").write_text(
            "---\ntype: guideline\ntrigger: when writing tests\n---\n\nAssert the important thing.\n"
        )
        result = run_retrieve(evolve_dir=d)
        assert "when writing tests" in result.stdout
