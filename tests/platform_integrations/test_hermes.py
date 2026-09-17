"""Tests for the Hermes memory-provider bundle and its installer.

Bundle shape, entity-format delegation, the installer, and importability live
here; the provider's runtime behaviour (recall, capture gating, tools) lives in
test_hermes_provider.py.
"""

import sys
from pathlib import Path

import pytest
import yaml

from _hermes_loader import HERMES_PLUGIN_ROOT as _HERMES_PLUGIN_ROOT
from _hermes_loader import HOST_STUBS as _HOST_STUBS
from _hermes_loader import is_stdlib_import as _is_stdlib_import
from _hermes_loader import load_module as _load_module

pytestmark = pytest.mark.platform_integrations

# The exact rendered bundle. Pinned so a new shared file under plugin-source/
# cannot silently fan into the Hermes bundle (target_excludes is opt-out).
#
# entity_io.py is the only shared lib module here: the other four are reachable
# only from skills or from EVOLVE.md's shell-out, neither of which Hermes ships.
# See PLATFORMS["hermes"]["target_excludes"] in build_plugins.py.
_EXPECTED_BUNDLE = {
    "README.md",
    "__init__.py",
    "backend.py",
    "guideline_gen.py",
    "lib/evolve-lite/__init__.py",
    "lib/evolve-lite/entity_io.py",
    "plugin.yaml",
    "trajectory_adapter.py",
}


def _bundle_files():
    return {
        p.relative_to(_HERMES_PLUGIN_ROOT).as_posix()
        for p in _HERMES_PLUGIN_ROOT.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def hermes_backend():
    """The rendered ``backend.py``. Stdlib-only, so no host stubs are needed."""
    return _load_module("_hermes_backend", _HERMES_PLUGIN_ROOT / "backend.py")


class TestHermesBundleStructure:
    def test_bundle_contains_exactly_the_expected_files(self):
        assert _bundle_files() == _EXPECTED_BUNDLE

    def test_no_evolve_lite_skills_shipped(self):
        # The Hermes provider drives the loop from MemoryProvider callbacks, not
        # from prompt-invoked skills, so the shared skills/ tree must not ship.
        assert not (_HERMES_PLUGIN_ROOT / "skills").exists()

    def test_plugin_yaml_declares_no_pip_dependencies(self):
        data = yaml.safe_load((_HERMES_PLUGIN_ROOT / "plugin.yaml").read_text())
        assert data["name"] == "evolve"
        assert data["pip_dependencies"] == [], "the Hermes bundle must stay install-free — no runtime pip dependency"


class TestHermesEntityIo:
    """The entity format comes from the shared lib, not a private copy.

    backend.py used to re-implement entity_io.py. These tests pin the
    delegation itself (so the copy cannot creep back) and the two places the
    Hermes wrappers deliberately differ from the shared functions.
    """

    def test_format_helpers_come_from_the_shared_module(self, hermes_backend):
        shared = _HERMES_PLUGIN_ROOT / "lib/evolve-lite/entity_io.py"
        for fn in (hermes_backend.entity_to_markdown, hermes_backend.markdown_to_entity):
            assert Path(fn.__code__.co_filename) == shared, f"{fn.__name__} is not the shared implementation"

    def test_bundled_lib_is_not_put_on_sys_path(self, hermes_backend):
        # The provider is imported into a long-lived host process, so it must add
        # nothing importable that unrelated code could pick up by accident. On
        # sys.path this directory captures every module name in it -- verified by
        # importing the bundle with a foreign `config` module already resident and
        # finding it untouched.
        assert str(hermes_backend._LIB_DIR) not in sys.path
        # Registered under a namespaced key, not a bare `entity_io`. (Asserting
        # the absence of the bare name would be order-dependent: the skill-script
        # tests legitimately import it that way, in a process the host never has.)
        assert "evolve_lite_entity_io" in sys.modules

    def test_backend_source_does_not_reimplement_the_format(self):
        source = (_HERMES_PLUGIN_ROOT / "backend.py").read_text()
        assert "_FRONTMATTER_KEYS" not in source, "frontmatter key order belongs to lib/entity_io.py alone"

    def test_slugify_tolerates_none(self, hermes_backend):
        # The shared slugify assumes a string and would raise on None; the
        # wrapper keeps the provider's historical tolerance. "entity" (not "")
        # is the shared function's own empty-input fallback.
        assert hermes_backend.slugify(None) == "entity"
        assert hermes_backend.slugify("Prefer uv run") == "prefer-uv-run"

    def test_entity_to_markdown_emits_ordered_non_empty_frontmatter(self, hermes_backend):
        md = hermes_backend.entity_to_markdown(
            {
                "type": "guideline",
                "trigger": "counting issues",
                "owner": "",
                "content": "Use the search API.",
                "rationale": "The list endpoint paginates.",
            }
        )
        assert md.startswith("---\ntype: guideline\ntrigger: counting issues\n---\n")
        assert "owner:" not in md
        assert "## Rationale\n\nThe list endpoint paginates." in md

    def test_markdown_round_trips(self, hermes_backend, tmp_path):
        entity = {
            "type": "guideline",
            "trigger": "counting issues",
            "content": "Use the search API.\n\nIt returns total_count.",
            "rationale": "The list endpoint paginates.",
        }
        path = tmp_path / "g.md"
        path.write_text(hermes_backend.entity_to_markdown(entity), encoding="utf-8")
        assert hermes_backend.markdown_to_entity(path) == entity

    def test_write_entity_file_uses_a_type_subdirectory(self, hermes_backend, tmp_path):
        path = hermes_backend.write_entity_file(tmp_path, {"type": "Guideline Note", "content": "Prefer uv run."})
        assert path.parent == tmp_path / "guideline-note"
        assert path.name == "prefer-uv-run.md"

    def test_write_entity_file_suffixes_on_collision(self, hermes_backend, tmp_path):
        entity = {"type": "guideline", "content": "Prefer uv run."}
        first = hermes_backend.write_entity_file(tmp_path, entity)
        second = hermes_backend.write_entity_file(tmp_path, entity)
        assert first.name == "prefer-uv-run.md"
        assert second.name == "prefer-uv-run-2.md"

    def test_write_entity_file_does_not_mutate_the_callers_entity(self, hermes_backend, tmp_path):
        # The shared implementation stamps the sanitized type onto the dict it is
        # handed; provider callers reuse their dicts, so the wrapper copies first.
        entity = {"type": "Guideline Note", "content": "Prefer uv run."}
        hermes_backend.write_entity_file(tmp_path, entity)
        assert entity["type"] == "Guideline Note"

    def test_lite_backend_round_trips_through_the_shared_writer(self, hermes_backend, tmp_path):
        backend = hermes_backend.LiteBackend(tmp_path)
        backend.save_guideline(
            content="Use the search API to count issues.",
            trigger="counting issues",
            rationale="The list endpoint paginates.",
        )
        results = backend.get_guidelines("how do I count issues", limit=5)
        assert len(results) == 1
        assert results[0]["content"] == "Use the search API to count issues."
        assert results[0]["trigger"] == "counting issues"
        assert results[0]["rationale"] == "The list endpoint paginates."

    def test_an_undecodable_entity_file_is_skipped_not_fatal(self, hermes_backend, tmp_path):
        # The provider swallows exceptions from retrieval, so one non-UTF-8 file
        # escaping _iter_entities would silently zero out recall for the whole
        # store. Only the bad file may be lost.
        backend = hermes_backend.LiteBackend(tmp_path)
        backend.save_guideline(content="Use the search API to count issues.", trigger="counting issues")
        (backend.entities_dir / "guideline" / "binary.md").write_bytes(b"---\ntype: guideline\n---\n\n\xff\xfe not utf-8\n")

        results = backend.get_guidelines("how do I count issues", limit=5)

        assert [r["content"] for r in results] == ["Use the search API to count issues."]


@pytest.fixture
def hermes_home(sandbox_home):
    """The sandboxed $HERMES_HOME the installer targets."""
    return sandbox_home / ".hermes"


@pytest.fixture
def hermes_plugin_dir(hermes_home):
    """Where the provider lands: $HERMES_HOME/plugins/evolve/."""
    return hermes_home / "plugins" / "evolve"


class TestHermesInstall:
    """Install copies the bundle to Hermes's documented user-plugin path.

    The install is GLOBAL — it lands under $HERMES_HOME regardless of --dir —
    because Hermes discovers memory providers from its own home, not per-repo.
    """

    def test_install_copies_provider_and_lib(self, install_runner, file_assertions, hermes_plugin_dir):
        install_runner.run("install", platform="hermes")

        file_assertions.assert_file_exists(hermes_plugin_dir / "__init__.py")
        file_assertions.assert_file_exists(hermes_plugin_dir / "backend.py")
        file_assertions.assert_file_exists(hermes_plugin_dir / "plugin.yaml")
        file_assertions.assert_file_exists(hermes_plugin_dir / "lib" / "evolve-lite" / "entity_io.py")

    def test_install_is_idempotent(self, install_runner, file_assertions, hermes_plugin_dir):
        install_runner.run("install", platform="hermes")
        install_runner.run("install", platform="hermes")

        file_assertions.assert_file_exists(hermes_plugin_dir / "__init__.py")

    def test_install_ignores_target_dir(self, install_runner, temp_project_dir, hermes_plugin_dir):
        # Global, not per-project: nothing may be written under the repo.
        install_runner.run("install", platform="hermes")

        assert hermes_plugin_dir.is_dir()
        assert not (temp_project_dir / ".hermes").exists()

    def test_install_honours_hermes_home(self, install_runner, tmp_path, file_assertions):
        elsewhere = tmp_path / "custom-hermes"
        install_runner.run("install", platform="hermes", env={"HERMES_HOME": str(elsewhere)})

        file_assertions.assert_file_exists(elsewhere / "plugins" / "evolve" / "__init__.py")

    def test_install_preserves_a_sibling_user_plugin(self, install_runner, file_assertions, hermes_home):
        # AGENTS.md:10 — never disturb user content.
        other = hermes_home / "plugins" / "my-own-plugin" / "plugin.yaml"
        file_assertions.write_text(other, "name: my-own-plugin\n")

        install_runner.run("install", platform="hermes")

        file_assertions.assert_file_unchanged(other, "name: my-own-plugin\n")

    def test_dry_run_writes_nothing(self, install_runner, hermes_plugin_dir):
        result = install_runner.run("install", platform="hermes", dry_run=True)

        assert "DRY RUN" in result.stdout
        assert not hermes_plugin_dir.exists()

    def test_uninstall_removes_only_the_evolve_plugin(self, install_runner, file_assertions, hermes_home, hermes_plugin_dir):
        other = hermes_home / "plugins" / "my-own-plugin" / "plugin.yaml"
        file_assertions.write_text(other, "name: my-own-plugin\n")
        install_runner.run("install", platform="hermes")
        file_assertions.assert_file_exists(hermes_plugin_dir / "__init__.py")

        install_runner.run("uninstall", platform="hermes")

        file_assertions.assert_dir_not_exists(hermes_plugin_dir)
        file_assertions.assert_file_unchanged(other, "name: my-own-plugin\n")

    def test_uninstall_keeps_the_guideline_store(self, install_runner, file_assertions, hermes_home):
        # Learned guidelines are user data; uninstall removes code, never the store.
        store = hermes_home / "evolve" / "entities" / "guideline" / "kept.md"
        file_assertions.write_text(store, "---\ntype: guideline\n---\n\nKeep me.\n")
        install_runner.run("install", platform="hermes")

        install_runner.run("uninstall", platform="hermes")

        file_assertions.assert_file_unchanged(store, "---\ntype: guideline\n---\n\nKeep me.\n")

    def test_uninstall_without_install_succeeds(self, install_runner):
        # Nothing to remove is not an error.
        install_runner.run("uninstall", platform="hermes")

    def test_status_reports_hermes(self, install_runner):
        install_runner.run("install", platform="hermes")
        result = install_runner.run("status")

        assert "Hermes" in result.stdout


class TestHermesProviderImports:
    """The bundle must import with only the two hard host dependencies stubbed."""

    def test_provider_imports_and_exposes_register(self):
        provider = _load_module(
            "_hermes_provider",
            _HERMES_PLUGIN_ROOT / "__init__.py",
            extra_syspath=[_HOST_STUBS],
        )
        assert hasattr(provider, "EvolveMemoryProvider")
        assert callable(provider.register)

    def test_provider_name_is_evolve(self):
        provider = _load_module(
            "_hermes_provider_name",
            _HERMES_PLUGIN_ROOT / "__init__.py",
            extra_syspath=[_HOST_STUBS],
        )
        assert provider.EvolveMemoryProvider().name == "evolve"

    def test_only_two_host_modules_are_hard_imports(self):
        """Everything beyond MemoryProvider + tool_error must stay lazy/guarded.

        The bundle is imported into a Hermes process, but it is also unit-tested
        outside one. Every extra module-level host import is another stub the
        tests must carry — and another way a Hermes refactor breaks the bundle.
        """
        source = (_HERMES_PLUGIN_ROOT / "__init__.py").read_text()
        module_level = [ln for ln in source.splitlines() if ln.startswith("from ") or ln.startswith("import ")]
        # Anything not resolvable from the stdlib or the bundle itself is a host
        # import; matching on names would miss a new one (e.g. `utils`).
        host_imports = [ln for ln in module_level if not ln.startswith("from .") and not _is_stdlib_import(ln)]
        assert host_imports == [
            "from agent.memory_provider import MemoryProvider",
            "from tools.registry import tool_error",
        ], f"unexpected module-level host imports: {host_imports}"
