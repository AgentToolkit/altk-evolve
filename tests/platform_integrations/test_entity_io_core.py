"""Tests for entity_io.py — slugify, serialization, write, and load functions.

The existing test_entity_io.py covers directory-resolution helpers. This file
covers the serialization and I/O functions needed by the sharing feature.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"),
)
import entity_io

pytestmark = [pytest.mark.platform_integrations, pytest.mark.unit]


class TestSlugify:
    def test_lowercases_and_replaces_spaces(self):
        assert entity_io.slugify("Hello World") == "hello-world"

    def test_strips_special_characters(self):
        assert entity_io.slugify("Use temp files for JSON transfer!") == "use-temp-files-for-json-transfer"

    def test_collapses_multiple_separators(self):
        assert entity_io.slugify("foo  --  bar") == "foo-bar"

    def test_truncates_at_max_length_on_word_boundary(self):
        long_text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        result = entity_io.slugify(long_text, max_length=30)
        assert len(result) <= 30
        assert not result.endswith("-")

    def test_empty_string_returns_entity(self):
        assert entity_io.slugify("") == "entity"

    def test_all_special_chars_returns_entity(self):
        assert entity_io.slugify("!!!") == "entity"


class TestUniqueFilename:
    def test_returns_slug_md_when_no_collision(self, temp_project_dir):
        path = entity_io.unique_filename(temp_project_dir, "my-guideline")
        assert path == temp_project_dir / "my-guideline.md"

    def test_increments_suffix_on_collision(self, temp_project_dir, file_assertions):
        file_assertions.write_text(temp_project_dir / "my-guideline.md", "")
        path = entity_io.unique_filename(temp_project_dir, "my-guideline")
        assert path == temp_project_dir / "my-guideline-2.md"

    def test_keeps_incrementing(self, temp_project_dir, file_assertions):
        file_assertions.write_text(temp_project_dir / "my-guideline.md", "")
        file_assertions.write_text(temp_project_dir / "my-guideline-2.md", "")
        path = entity_io.unique_filename(temp_project_dir, "my-guideline")
        assert path == temp_project_dir / "my-guideline-3.md"


class TestEntityMarkdownRoundtrip:
    def test_basic_roundtrip(self, tmp_path):
        entity = {
            "type": "guideline",
            "trigger": "when writing tests",
            "content": "Prefer real databases over mocks.",
            "rationale": "Mocks hide real integration bugs.",
        }
        path = tmp_path / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["content"] == "Prefer real databases over mocks."
        assert result["type"] == "guideline"
        assert result["trigger"] == "when writing tests"
        assert result["rationale"] == "Mocks hide real integration bugs."

    def test_entity_without_optional_fields(self, tmp_path):
        entity = {"type": "guideline", "content": "Keep functions small."}
        path = tmp_path / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["content"] == "Keep functions small."
        assert "rationale" not in result

    def test_visibility_owner_published_at_preserved(self, tmp_path):
        entity = {
            "type": "guideline",
            "content": "Document public APIs.",
            "visibility": "public",
            "owner": "alice",
            "published_at": "2026-01-01T00:00:00Z",
        }
        path = tmp_path / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["visibility"] == "public"
        assert result["owner"] == "alice"
        assert result["published_at"] == "2026-01-01T00:00:00Z"

    def test_file_without_frontmatter(self, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("Some content here.")
        result = entity_io.markdown_to_entity(path)
        assert result["content"] == "Some content here."


class TestWriteEntityFile:
    def test_writes_file_in_type_subdirectory(self, tmp_path):
        entity = {"type": "guideline", "content": "Use semantic versioning."}
        path = entity_io.write_entity_file(tmp_path, entity)
        assert path.parent == tmp_path / "guideline"
        assert path.suffix == ".md"
        assert path.exists()

    def test_preference_type_goes_in_preference_dir(self, tmp_path):
        entity = {"type": "preference", "content": "Prefer tabs over spaces."}
        path = entity_io.write_entity_file(tmp_path, entity)
        assert path.parent == tmp_path / "preference"

    def test_invalid_type_defaults_to_guideline(self, tmp_path):
        entity = {"type": "badtype", "content": "Some content."}
        path = entity_io.write_entity_file(tmp_path, entity)
        assert path.parent == tmp_path / "guideline"

    def test_written_file_is_readable(self, tmp_path):
        entity = {"type": "guideline", "content": "Write clear commit messages."}
        path = entity_io.write_entity_file(tmp_path, entity)
        result = entity_io.markdown_to_entity(path)
        assert result["content"] == "Write clear commit messages."

    def test_no_collision_on_duplicate_slug(self, tmp_path):
        entity = {"type": "guideline", "content": "No magic numbers."}
        path1 = entity_io.write_entity_file(tmp_path, entity)
        path2 = entity_io.write_entity_file(tmp_path, entity)
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()


class TestLoadAllEntities:
    def test_loads_from_nested_type_dirs(self, temp_project_dir):
        (temp_project_dir / "guideline").mkdir()
        (temp_project_dir / "guideline" / "guideline.md").write_text("---\ntype: guideline\n---\n\nKeep it simple.\n")
        (temp_project_dir / "preference").mkdir()
        (temp_project_dir / "preference" / "pref.md").write_text("---\ntype: preference\n---\n\nUse snake_case.\n")
        entities = entity_io.load_all_entities(temp_project_dir)
        contents = {e["content"] for e in entities}
        assert "Keep it simple." in contents
        assert "Use snake_case." in contents

    def test_skips_files_without_content(self, tmp_path):
        (tmp_path / "guideline").mkdir()
        (tmp_path / "guideline" / "empty.md").write_text("---\ntype: guideline\n---\n\n")
        assert entity_io.load_all_entities(tmp_path) == []

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert entity_io.load_all_entities(tmp_path) == []


class TestManifestLoading:
    def test_load_manifest_reads_frontmatter_only(self, temp_project_dir, monkeypatch):
        monkeypatch.chdir(temp_project_dir)
        path = temp_project_dir / ".evolve" / "entities" / "guideline" / "guideline.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\ntype: guideline\ntrigger: when writing tests\n---\n\nBody content that should not matter.\n\n## Rationale\n\nStill ignored.\n"
        )

        manifest = entity_io.load_manifest(temp_project_dir / ".evolve" / "entities")

        assert manifest == [
            {
                "path": ".evolve/entities/guideline/guideline.md",
                "type": "guideline",
                "trigger": "when writing tests",
            }
        ]

    def test_load_manifest_skips_symlinks_and_missing_trigger(self, temp_project_dir, monkeypatch):
        monkeypatch.chdir(temp_project_dir)
        root = temp_project_dir / ".evolve" / "entities" / "guideline"
        root.mkdir(parents=True)
        real_file = root / "real.md"
        real_file.write_text("---\ntype: guideline\ntrigger: when testing\n---\n\nReal content.\n")
        (root / "link.md").symlink_to(real_file)
        (root / "missing-trigger.md").write_text("---\ntype: guideline\n---\n\nIgnored.\n")

        manifest = entity_io.load_manifest(temp_project_dir / ".evolve" / "entities")

        assert manifest == [
            {
                "path": ".evolve/entities/guideline/real.md",
                "type": "guideline",
                "trigger": "when testing",
            }
        ]

    def test_dedupe_manifest_entries_is_deterministic(self):
        entries = [
            {"path": ".evolve/public/guideline/b.md", "type": "guideline", "trigger": "beta"},
            {"path": ".evolve/entities/guideline/a.md", "type": "guideline", "trigger": "alpha"},
            {"path": ".evolve/public/guideline/b.md", "type": "guideline", "trigger": "beta"},
        ]

        assert entity_io.dedupe_manifest_entries(entries) == [
            {"path": ".evolve/entities/guideline/a.md", "type": "guideline", "trigger": "alpha"},
            {"path": ".evolve/public/guideline/b.md", "type": "guideline", "trigger": "beta"},
        ]
