"""Tests for Codex manifest-first recall output."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_REPO_ROOT = Path(__file__).parent.parent.parent
CODEX_RETRIEVE_SCRIPT = (
    _REPO_ROOT / "platform-integrations/codex/plugins/evolve-lite/skills/evolve-lite/recall/scripts/retrieve_entities.py"
)
HOOK_INPUT = json.dumps({"prompt": "How do I write clean code?"})


def run_retrieve(project_dir, evolve_dir, stdin_data=None):
    env = {**os.environ, "EVOLVE_DIR": str(evolve_dir)}
    return subprocess.run(
        [sys.executable, str(CODEX_RETRIEVE_SCRIPT)],
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
def evolve_dir(temp_project_dir):
    d = temp_project_dir / ".evolve"

    own_dir = d / "entities" / "guideline"
    own_dir.mkdir(parents=True)
    (own_dir / "guideline.md").write_text("---\ntype: guideline\ntrigger: when refactoring functions\n---\n\nKeep functions small.\n")

    sub_dir = d / "entities" / "subscribed" / "alice" / "guideline"
    sub_dir.mkdir(parents=True)
    (sub_dir / "alice-guideline.md").write_text(
        "---\ntype: guideline\ntrigger: when adding coverage\nowner: alice\nvisibility: public\n---\n\nAlways write tests.\n"
    )

    public_dir = d / "public" / "guideline"
    public_dir.mkdir(parents=True)
    (public_dir / "published-guideline.md").write_text(
        "---\ntype: guideline\ntrigger: when documenting edge cases\nvisibility: public\nsource: alice/evolve-guidelines\n---\n\nDocument edge cases.\n"
    )

    return d


class TestCodexRetrieveManifest:
    def test_outputs_manifest_header_and_json_entries(self, temp_project_dir, evolve_dir):
        result = run_retrieve(temp_project_dir, evolve_dir)

        assert result.returncode == 0
        assert "Evolve entity manifest for this task" in result.stdout
        assert "Read only the files whose trigger looks relevant" in result.stdout
        assert parse_manifest_lines(result.stdout) == [
            {
                "path": ".evolve/entities/guideline/guideline.md",
                "type": "guideline",
                "trigger": "when refactoring functions",
            },
            {
                "path": ".evolve/entities/subscribed/alice/guideline/alice-guideline.md",
                "type": "guideline",
                "trigger": "when adding coverage",
            },
            {
                "path": ".evolve/public/guideline/published-guideline.md",
                "type": "guideline",
                "trigger": "when documenting edge cases",
            },
        ]

    def test_does_not_emit_full_entity_bodies_or_extra_fields(self, temp_project_dir, evolve_dir):
        result = run_retrieve(temp_project_dir, evolve_dir)

        assert "Keep functions small." not in result.stdout
        assert "Always write tests." not in result.stdout
        assert "Document edge cases." not in result.stdout
        assert "[from:" not in result.stdout
        assert "visibility" not in result.stdout
        assert "source" not in result.stdout

    def test_output_is_deterministic_and_deduplicated(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        guideline_dir = evolve_dir / "entities" / "guideline"
        guideline_dir.mkdir(parents=True)
        (guideline_dir / "b.md").write_text("---\ntype: guideline\ntrigger: beta\n---\n\nB body.\n")
        (guideline_dir / "a.md").write_text("---\ntype: guideline\ntrigger: alpha\n---\n\nA body.\n")

        result = run_retrieve(temp_project_dir, evolve_dir)

        assert parse_manifest_lines(result.stdout) == [
            {"path": ".evolve/entities/guideline/a.md", "type": "guideline", "trigger": "alpha"},
            {"path": ".evolve/entities/guideline/b.md", "type": "guideline", "trigger": "beta"},
        ]

    def test_skips_symlinked_markdown_entities(self, temp_project_dir):
        evolve_dir = temp_project_dir / ".evolve"
        gdir = evolve_dir / "entities" / "subscribed" / "alice" / "guideline"
        gdir.mkdir(parents=True)
        real_file = gdir / "real.md"
        real_file.write_text("---\ntype: guideline\ntrigger: when testing\n---\n\nReal content.\n")
        (gdir / "link.md").symlink_to(real_file)

        result = run_retrieve(temp_project_dir, evolve_dir)

        assert result.returncode == 0
        assert parse_manifest_lines(result.stdout) == [
            {
                "path": ".evolve/entities/subscribed/alice/guideline/real.md",
                "type": "guideline",
                "trigger": "when testing",
            }
        ]

    def test_handles_invalid_json_stdin_gracefully(self, temp_project_dir, evolve_dir):
        result = run_retrieve(temp_project_dir, evolve_dir, stdin_data="not valid json")

        assert result.returncode == 0
        assert result.stdout.strip() == ""
