"""`evolve entities import` and `export`, against a real filesystem backend."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from altk_evolve.cli.cli import app
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.exceptions import EvolveException

pytestmark = pytest.mark.unit

runner = CliRunner()

RULE = "Before committing, add an entry under ## Unreleased in CHANGES.md."
WORKFLOW = "Edit schema/models.yaml, then run make generate."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    client = EvolveClient(EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "entities"))))
    with patch("altk_evolve.cli.cli.get_client", return_value=client):
        yield client


def guidelines(client, namespace="cortex"):
    return client.get_all_entities(namespace, filters={"type": "guideline"}, limit=100)


def write(tmp_path, data, name="guidelines.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_import_loads_guidelines_into_a_new_namespace(client, tmp_path):
    path = write(
        tmp_path,
        {"guidelines": [{"content": RULE, "metadata": {"trigger": "schema changed", "task_description": "add a field"}}, WORKFLOW]},
    )

    result = runner.invoke(app, ["entities", "import", "cortex", str(path)])

    assert result.exit_code == 0, result.output
    stored = {g.content: g for g in guidelines(client)}
    assert set(stored) == {RULE, WORKFLOW}
    assert stored[RULE].metadata["trigger"] == "schema changed"
    assert stored[RULE].metadata["task_description"] == "add a field"


def test_import_refuses_a_namespace_that_already_has_guidelines(client, tmp_path):
    path = write(tmp_path, [RULE])
    runner.invoke(app, ["entities", "import", "cortex", str(path)])

    result = runner.invoke(app, ["entities", "import", "cortex", str(path)])

    assert result.exit_code == 1
    assert "--replace or --append" in result.output
    assert len(guidelines(client)) == 1


def test_import_replace_and_append(client, tmp_path):
    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE], "a.json"))])
    other = write(tmp_path, [WORKFLOW], "b.json")

    assert runner.invoke(app, ["entities", "import", "cortex", str(other), "--replace"]).exit_code == 0
    assert [g.content for g in guidelines(client)] == [WORKFLOW]

    assert runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE], "c.json")), "--append"]).exit_code == 0
    assert {g.content for g in guidelines(client)} == {RULE, WORKFLOW}


def test_replace_keeps_the_old_entities_when_the_write_fails(client, tmp_path, monkeypatch):
    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE], "a.json"))])

    def fail(*args, **kwargs):
        raise EvolveException("backend down")

    monkeypatch.setattr(client, "update_entities", fail)
    result = runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [WORKFLOW], "b.json")), "--replace"])

    assert result.exit_code == 1
    assert [g.content for g in guidelines(client)] == [RULE]


def test_import_writes_entities_as_they_are_without_conflict_resolution(client, tmp_path, monkeypatch):
    calls = []
    update = client.update_entities
    monkeypatch.setattr(client, "update_entities", lambda *a, **kw: calls.append(kw) or update(*a, **kw))

    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE]))])

    assert calls == [{"enable_conflict_resolution": False}]


def test_import_drops_metadata_a_caller_cannot_set(client, tmp_path):
    path = write(tmp_path, [{"content": RULE, "metadata": {"merged_from": [{"id": "9"}], "trigger": "t"}}])

    result = runner.invoke(app, ["entities", "import", "cortex", str(path)])

    [stored] = guidelines(client)
    assert "merged_from" not in stored.metadata
    assert stored.metadata["trigger"] == "t"
    assert "Dropped 'merged_from' from 1 entries" in result.output


@pytest.mark.parametrize(
    "data",
    [{"nothing": []}, [{"metadata": {}}], [{"content": "x", "metadata": ["not", "an", "object"]}]],
)
def test_import_rejects_a_malformed_file_before_writing(client, tmp_path, data):
    result = runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, data))])

    assert result.exit_code == 1
    assert not client.namespace_exists("cortex")


def test_export_then_import_round_trips(client, tmp_path):
    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [{"content": RULE, "metadata": {"trigger": "t"}}]))])
    exported = tmp_path / "export.json"

    result = runner.invoke(app, ["entities", "export", "cortex", "--output", str(exported)])
    assert result.exit_code == 0, result.output
    document = json.loads(exported.read_text())
    assert document["namespace"] == "cortex"
    assert [(e["type"], e["content"], e["metadata"]["trigger"]) for e in document["entities"]] == [("guideline", RULE, "t")]

    assert runner.invoke(app, ["entities", "import", "copy", str(exported)]).exit_code == 0
    [copied] = guidelines(client, "copy")
    assert (copied.content, copied.metadata["trigger"]) == (RULE, "t")


def test_export_to_standard_output_is_only_json(client, tmp_path):
    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE, WORKFLOW]))])

    result = runner.invoke(app, ["entities", "export", "cortex"])

    assert result.exit_code == 0
    assert {e["content"] for e in json.loads(result.output)["entities"]} == {RULE, WORKFLOW}


def test_export_to_an_unwritable_file_fails(client, tmp_path):
    runner.invoke(app, ["entities", "import", "cortex", str(write(tmp_path, [RULE]))])

    result = runner.invoke(app, ["entities", "export", "cortex", "-o", str(tmp_path / "missing" / "out.json")])

    assert result.exit_code == 1
    assert "Can't write" in result.output


def test_export_of_a_missing_namespace_fails(client):
    result = runner.invoke(app, ["entities", "export", "nope"])

    assert result.exit_code == 1
