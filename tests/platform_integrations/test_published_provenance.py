"""Resolve published native memory IDs without changing trajectory lookup."""

import pytest

from test_provenance import _provenance_module

pytestmark = pytest.mark.platform_integrations


@pytest.mark.parametrize("scope,owner,found", [("write", "alice", True), ("read", "alice", False), ("write", "bob", False)])
def test_published_native_id_resolves_only_publishers_write_clone(tmp_path, scope, owner, found):
    evolve = tmp_path / ".evolve"
    published = evolve / "entities/subscribed/team/feedback/style.md"
    published.parent.mkdir(parents=True)
    published.write_text(f"---\ntype: feedback\nowner: {owner}\n---\nPublished advice\n")
    (tmp_path / "evolve.config.yaml").write_text(
        f"identity:\n  user: alice\nrepos:\n  - name: team\n    scope: {scope}\n    remote: git@example.com:team/memory.git\n"
    )
    path, excerpt = _provenance_module()._read_entity(evolve, "feedback/style", project_root=tmp_path)
    assert (excerpt is not None) == found
    if found:
        assert path == published
        assert "Published advice" in excerpt
    private = evolve / "entities/feedback/style.md"
    private.parent.mkdir()
    private.write_text("New private mirror")
    assert _provenance_module()._read_entity(evolve, "feedback/style", project_root=tmp_path)[1] == "New private mirror"


def test_published_native_id_ambiguity_is_not_guessed(tmp_path):
    evolve = tmp_path / ".evolve"
    config = "identity:\n  user: alice\nrepos:\n"
    for repo in ("one", "two"):
        path = evolve / f"entities/subscribed/{repo}/feedback/style.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ntype: feedback\nowner: alice\n---\nAdvice\n")
        config += f"  - name: {repo}\n    scope: write\n    remote: git@example.com:team/{repo}.git\n"
    (tmp_path / "evolve.config.yaml").write_text(config)
    assert _provenance_module()._read_entity(evolve, "feedback/style", project_root=tmp_path)[1] is None
