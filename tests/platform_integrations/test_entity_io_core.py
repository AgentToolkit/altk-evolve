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

pytestmark = pytest.mark.platform_integrations


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
        path = entity_io.unique_filename(temp_project_dir, "my-tip")
        assert path == temp_project_dir / "my-tip.md"

    def test_increments_suffix_on_collision(self, temp_project_dir):
        (temp_project_dir / "my-tip.md").touch()
        path = entity_io.unique_filename(temp_project_dir, "my-tip")
        assert path == temp_project_dir / "my-tip-2.md"

    def test_keeps_incrementing(self, temp_project_dir):
        (temp_project_dir / "my-tip.md").touch()
        (temp_project_dir / "my-tip-2.md").touch()
        path = entity_io.unique_filename(temp_project_dir, "my-tip")
        assert path == temp_project_dir / "my-tip-3.md"


class TestEntityMarkdownRoundtrip:
    def test_basic_roundtrip(self, temp_project_dir):
        entity = {
            "type": "guideline",
            "trigger": "when writing tests",
            "content": "Prefer real databases over mocks.",
            "rationale": "Mocks hide real integration bugs.",
        }
        path = temp_project_dir / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["content"] == "Prefer real databases over mocks."
        assert result["type"] == "guideline"
        assert result["trigger"] == "when writing tests"
        assert result["rationale"] == "Mocks hide real integration bugs."

    def test_entity_without_optional_fields(self, temp_project_dir):
        entity = {"type": "guideline", "content": "Keep functions small."}
        path = temp_project_dir / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["content"] == "Keep functions small."
        assert "rationale" not in result

    def test_visibility_owner_published_at_preserved(self, temp_project_dir):
        entity = {
            "type": "guideline",
            "content": "Document public APIs.",
            "visibility": "public",
            "owner": "alice",
            "published_at": "2026-01-01T00:00:00Z",
        }
        path = temp_project_dir / "test.md"
        path.write_text(entity_io.entity_to_markdown(entity))
        result = entity_io.markdown_to_entity(path)

        assert result["visibility"] == "public"
        assert result["owner"] == "alice"
        assert result["published_at"] == "2026-01-01T00:00:00Z"

    def test_file_without_frontmatter(self, temp_project_dir):
        path = temp_project_dir / "test.md"
        path.write_text("Some content here.")
        result = entity_io.markdown_to_entity(path)
        assert result["content"] == "Some content here."


class TestWriteEntityFile:
    def test_writes_file_in_type_subdirectory(self, temp_project_dir):
        entity = {"type": "guideline", "content": "Use semantic versioning."}
        path = entity_io.write_entity_file(temp_project_dir, entity)
        assert path.parent == temp_project_dir / "guideline"
        assert path.suffix == ".md"
        assert path.exists()

    def test_preference_type_goes_in_preference_dir(self, temp_project_dir):
        entity = {"type": "preference", "content": "Prefer tabs over spaces."}
        path = entity_io.write_entity_file(temp_project_dir, entity)
        assert path.parent == temp_project_dir / "preference"

    def test_invalid_type_defaults_to_guideline(self, temp_project_dir):
        entity = {"type": "badtype", "content": "Some content."}
        path = entity_io.write_entity_file(temp_project_dir, entity)
        assert path.parent == temp_project_dir / "guideline"

    def test_written_file_is_readable(self, temp_project_dir):
        entity = {"type": "guideline", "content": "Write clear commit messages."}
        path = entity_io.write_entity_file(temp_project_dir, entity)
        result = entity_io.markdown_to_entity(path)
        assert result["content"] == "Write clear commit messages."

    def test_no_collision_on_duplicate_slug(self, temp_project_dir):
        entity = {"type": "guideline", "content": "No magic numbers."}
        path1 = entity_io.write_entity_file(temp_project_dir, entity)
        path2 = entity_io.write_entity_file(temp_project_dir, entity)
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()


class TestLoadAllEntities:
    def test_loads_from_nested_type_dirs(self, temp_project_dir):
        (temp_project_dir / "guideline").mkdir()
        (temp_project_dir / "guideline" / "tip.md").write_text("---\ntype: guideline\n---\n\nKeep it simple.\n")
        (temp_project_dir / "preference").mkdir()
        (temp_project_dir / "preference" / "pref.md").write_text("---\ntype: preference\n---\n\nUse snake_case.\n")
        entities = entity_io.load_all_entities(temp_project_dir)
        contents = {e["content"] for e in entities}
        assert "Keep it simple." in contents
        assert "Use snake_case." in contents

    def test_skips_files_without_content(self, temp_project_dir):
        (temp_project_dir / "guideline").mkdir()
        (temp_project_dir / "guideline" / "empty.md").write_text("---\ntype: guideline\n---\n\n")
        assert entity_io.load_all_entities(temp_project_dir) == []

    def test_empty_directory_returns_empty_list(self, temp_project_dir):
        assert entity_io.load_all_entities(temp_project_dir) == []
