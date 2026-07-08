"""Tests for entity_io.py — slugify, serialization, write, and load functions.

The existing test_entity_io.py covers directory-resolution helpers. This file
covers the serialization and I/O functions needed by the sharing feature.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_CLAUDE_PLUGIN = Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite"
sys.path.insert(0, str(_CLAUDE_PLUGIN / "lib/evolve-lite"))
import entity_io  # noqa: E402

pytestmark = [pytest.mark.platform_integrations, pytest.mark.unit]


def _load_adapt_memory():
    """Load the rendered Claude adapt_memory.py as a module.

    Its lib resolution only works in the rendered tree (it walks up to find
    ``lib/evolve-lite/entity_io.py``), so we import the rendered copy.
    """
    path = _CLAUDE_PLUGIN / "skills/evolve-lite/adapt-memory/scripts/adapt_memory.py"
    spec = importlib.util.spec_from_file_location("adapt_memory_rendered", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


class TestClaudeProjectSlug:
    def test_maps_known_path_to_dash_form(self):
        # The single source of truth shared by doctor.py and adapt_memory.py.
        assert entity_io.claude_project_slug("/Users/x/evolve-smoke-test2") == "-Users-x-evolve-smoke-test2"

    def test_memory_dir_under_given_home(self, tmp_path):
        home = tmp_path / "home"
        memory_dir = entity_io.claude_memory_dir("/Users/x/proj", home=home)
        assert memory_dir == home / ".claude" / "projects" / "-Users-x-proj" / "memory"


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

    def test_arbitrary_type_goes_in_its_own_dir(self, tmp_path):
        entity = {"type": "feedback", "content": "Some content."}
        path = entity_io.write_entity_file(tmp_path, entity)
        assert path.parent == tmp_path / "feedback"

    def test_type_is_sanitized_for_filesystem_safety(self, tmp_path):
        entity = {"type": "User Preference!", "content": "Some content."}
        path = entity_io.write_entity_file(tmp_path, entity)
        assert path.parent == tmp_path / "user-preference"
        assert entity["type"] == "user-preference"

    def test_empty_or_invalid_type_defaults_to_guideline(self, tmp_path):
        for bad_type in ("", "   ", "!!!"):
            entity = {"type": bad_type, "content": "Some content."}
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

    def test_explicit_filename_default_mode_still_suffixes_on_collision(self, tmp_path):
        # Default (overwrite=False) behavior is unchanged even with an
        # explicit filename: a second write gets a -2 suffix.
        entity = {"type": "feedback", "content": "First."}
        path1 = entity_io.write_entity_file(tmp_path, entity, filename="my-slug")
        path2 = entity_io.write_entity_file(tmp_path, {"type": "feedback", "content": "Second."}, filename="my-slug")
        assert path1 == tmp_path / "feedback" / "my-slug.md"
        assert path2 == tmp_path / "feedback" / "my-slug-2.md"

    def test_overwrite_mode_writes_deterministic_path_in_place(self, tmp_path):
        path1 = entity_io.write_entity_file(tmp_path, {"type": "feedback", "content": "First."}, filename="my-slug", overwrite=True)
        path2 = entity_io.write_entity_file(tmp_path, {"type": "feedback", "content": "Second."}, filename="my-slug", overwrite=True)
        assert path1 == path2 == tmp_path / "feedback" / "my-slug.md"
        assert "Second." in path2.read_text()
        assert not (tmp_path / "feedback" / "my-slug-2.md").exists()


class TestAdaptMemory:
    """Integration tests against the rendered Claude adapt_memory.py."""

    def _write_native(self, tmp_path, name, mem_type, body, description=None):
        lines = ["---"]
        if name is not None:
            lines.append(f"name: {name}")
        if description is not None:
            lines.append(f"description: {description}")
        lines += ["metadata:", f"  type: {mem_type}", "---", "", body, ""]
        native = tmp_path / "memory.md"
        native.write_text("\n".join(lines), encoding="utf-8")
        return native

    def _run(self, adapt, native, mem_type, trigger, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["adapt_memory.py", str(native), "--type", mem_type, "--trigger", trigger])
        adapt.main()

    def _run_argv(self, adapt, argv, monkeypatch, cwd):
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(sys, "argv", ["adapt_memory.py", *argv])
        adapt.main()

    def test_id_is_type_slash_name_and_native_path_stamped(self, tmp_path, monkeypatch, capsys):
        adapt = _load_adapt_memory()
        native = self._write_native(tmp_path, "my-fact", "feedback", "Always rebase.", "A short hook")
        self._run(adapt, native, "feedback", "when rebasing", monkeypatch, tmp_path)

        out = capsys.readouterr().out
        assert "Entity id: feedback/my-fact" in out

        entity_file = tmp_path / ".evolve" / "entities" / "feedback" / "my-fact.md"
        assert entity_file.exists()
        parsed = entity_io.markdown_to_entity(entity_file)
        assert parsed["native_path"] == str(native)
        assert parsed["source"] == "native-memory"
        assert parsed["type"] == "feedback"

    def test_deterministic_overwrite_on_same_name_and_type(self, tmp_path, monkeypatch, capsys):
        adapt = _load_adapt_memory()
        native = self._write_native(tmp_path, "my-fact", "feedback", "First version.")
        self._run(adapt, native, "feedback", "trig", monkeypatch, tmp_path)
        capsys.readouterr()

        native.write_text("---\nname: my-fact\nmetadata:\n  type: feedback\n---\n\nSecond version.\n", encoding="utf-8")
        self._run(adapt, native, "feedback", "trig", monkeypatch, tmp_path)

        feedback_dir = tmp_path / ".evolve" / "entities" / "feedback"
        files = sorted(p.name for p in feedback_dir.glob("*.md"))
        assert files == ["my-fact.md"]  # no my-fact-2.md
        assert "Second version." in (feedback_dir / "my-fact.md").read_text()

    def test_falls_back_to_content_slug_when_name_missing(self, tmp_path, monkeypatch, capsys):
        adapt = _load_adapt_memory()
        native = self._write_native(tmp_path, None, "project", "Use deterministic builds everywhere.")
        self._run(adapt, native, "project", "when building", monkeypatch, tmp_path)

        out = capsys.readouterr().out
        expected_slug = entity_io.slugify("Use deterministic builds everywhere.")
        assert f"Entity id: project/{expected_slug}" in out
        assert (tmp_path / ".evolve" / "entities" / "project" / f"{expected_slug}.md").exists()

    def _seed_native_dir(self, sandbox_home, cwd):
        """Create the project's native memory dir under the sandbox HOME and
        return it. The dir name is derived exactly as the script does."""
        memory_dir = entity_io.claude_memory_dir(cwd, home=sandbox_home)
        memory_dir.mkdir(parents=True, exist_ok=True)
        return memory_dir

    def test_auto_locate_picks_newest_non_memory_md(self, tmp_path, monkeypatch, capsys, sandbox_home):
        # The script auto-locates the newest *.md (excluding MEMORY.md) under
        # ~/.claude/projects/<slug-of-cwd>/memory/ when no path is passed.
        adapt = _load_adapt_memory()
        memory_dir = self._seed_native_dir(sandbox_home, tmp_path)

        (memory_dir / "MEMORY.md").write_text("# index\n", encoding="utf-8")
        older = memory_dir / "old-fact.md"
        older.write_text("---\nname: old-fact\nmetadata:\n  type: reference\n---\n\nOld body.\n", encoding="utf-8")
        newer = memory_dir / "new-fact.md"
        newer.write_text("---\nname: new-fact\nmetadata:\n  type: feedback\n---\n\nNew body.\n", encoding="utf-8")
        # Make the ordering unambiguous regardless of write speed.
        os.utime(older, (1_000_000, 1_000_000))
        os.utime(newer, (2_000_000, 2_000_000))

        self._run_argv(adapt, ["--trigger", "when X happens"], monkeypatch, tmp_path)

        out = capsys.readouterr().out
        # Newest non-MEMORY.md mirrored; type inferred from its frontmatter.
        assert "Entity id: feedback/new-fact" in out
        entity_file = tmp_path / ".evolve" / "entities" / "feedback" / "new-fact.md"
        assert entity_file.exists()
        parsed = entity_io.markdown_to_entity(entity_file)
        assert parsed["native_path"] == str(newer.resolve())
        assert parsed["trigger"] == "when X happens"
        # MEMORY.md was excluded; old-fact was not the newest.
        assert not (tmp_path / ".evolve" / "entities" / "reference" / "old-fact.md").exists()

    def test_type_default_project_when_no_frontmatter_type(self, tmp_path, monkeypatch, capsys, sandbox_home):
        adapt = _load_adapt_memory()
        memory_dir = self._seed_native_dir(sandbox_home, tmp_path)
        (memory_dir / "MEMORY.md").write_text("# index\n", encoding="utf-8")
        (memory_dir / "fact.md").write_text("---\nname: fact\n---\n\nNo type here.\n", encoding="utf-8")

        self._run_argv(adapt, ["--trigger", "trig"], monkeypatch, tmp_path)

        out = capsys.readouterr().out
        assert "Entity id: project/fact" in out

    def test_type_override_wins_over_frontmatter(self, tmp_path, monkeypatch, capsys, sandbox_home):
        adapt = _load_adapt_memory()
        memory_dir = self._seed_native_dir(sandbox_home, tmp_path)
        (memory_dir / "MEMORY.md").write_text("# index\n", encoding="utf-8")
        (memory_dir / "fact.md").write_text("---\nname: fact\nmetadata:\n  type: feedback\n---\n\nBody.\n", encoding="utf-8")

        self._run_argv(adapt, ["--type", "user", "--trigger", "trig"], monkeypatch, tmp_path)

        out = capsys.readouterr().out
        assert "Entity id: user/fact" in out

    def test_explicit_memory_path_still_works(self, tmp_path, monkeypatch, capsys, sandbox_home):
        # Back-compat: an explicit path bypasses auto-location entirely.
        adapt = _load_adapt_memory()
        native = self._write_native(tmp_path, "my-fact", "feedback", "Explicit path body.")

        self._run_argv(adapt, [str(native), "--type", "feedback", "--trigger", "trig"], monkeypatch, tmp_path)

        out = capsys.readouterr().out
        assert "Entity id: feedback/my-fact" in out

    def test_error_when_memory_dir_absent_and_no_path(self, tmp_path, monkeypatch, capsys, sandbox_home):
        adapt = _load_adapt_memory()
        # Do NOT seed the native dir — auto-location should fail clearly.
        with pytest.raises(SystemExit) as exc:
            self._run_argv(adapt, ["--trigger", "trig"], monkeypatch, tmp_path)
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "No native memory found" in err

    def test_error_when_memory_dir_empty_and_no_path(self, tmp_path, monkeypatch, capsys, sandbox_home):
        adapt = _load_adapt_memory()
        memory_dir = self._seed_native_dir(sandbox_home, tmp_path)
        # Only MEMORY.md present — no eligible memory to mirror.
        (memory_dir / "MEMORY.md").write_text("# index\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            self._run_argv(adapt, ["--trigger", "trig"], monkeypatch, tmp_path)
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "No native memory found" in err


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
