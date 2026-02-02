"""Tests for Kaizen CLI commands."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from kaizen.cli.cli import app
from kaizen.schema.core import Namespace, RecordedEntity
from kaizen.schema.conflict_resolution import EntityUpdate
from kaizen.schema.exceptions import (
    NamespaceAlreadyExistsException,
    NamespaceNotFoundException,
    KaizenException,
)


runner = CliRunner()


@pytest.fixture
def mock_client():
    """Create a mock KaizenClient."""
    with patch("kaizen.cli.cli.get_client") as mock_get_client:
        client = MagicMock()
        mock_get_client.return_value = client
        yield client


# =============================================================================
# Namespace Commands Tests
# =============================================================================


@pytest.mark.unit
class TestNamespacesList:
    """Tests for 'kaizen namespaces list' command."""

    def test_list_namespaces_empty(self, mock_client):
        """Test listing namespaces when none exist."""
        mock_client.all_namespaces.return_value = []

        result = runner.invoke(app, ["namespaces", "list"])

        assert result.exit_code == 0
        assert "No namespaces found" in result.stdout

    def test_list_namespaces_with_results(self, mock_client):
        """Test listing namespaces with results."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.all_namespaces.return_value = [
            Namespace(id="test_ns", created_at=created_at, num_entities=5),
            Namespace(id="another_ns", created_at=created_at, num_entities=10),
        ]

        result = runner.invoke(app, ["namespaces", "list"])

        assert result.exit_code == 0
        assert "test_ns" in result.stdout
        assert "another_ns" in result.stdout
        assert "5" in result.stdout
        assert "10" in result.stdout

    def test_list_namespaces_with_limit(self, mock_client):
        """Test listing namespaces with custom limit."""
        mock_client.all_namespaces.return_value = []

        runner.invoke(app, ["namespaces", "list", "--limit", "5"])

        mock_client.all_namespaces.assert_called_once_with(limit=5)


@pytest.mark.unit
class TestNamespacesCreate:
    """Tests for 'kaizen namespaces create' command."""

    def test_create_namespace_success(self, mock_client):
        """Test creating a new namespace successfully."""
        created_at = datetime.datetime.now(datetime.UTC)
        mock_client.create_namespace.return_value = Namespace(id="new_namespace", created_at=created_at)

        result = runner.invoke(app, ["namespaces", "create", "new_namespace"])

        assert result.exit_code == 0
        assert "Created namespace" in result.stdout
        assert "new_namespace" in result.stdout
        mock_client.create_namespace.assert_called_once_with("new_namespace")

    def test_create_namespace_already_exists(self, mock_client):
        """Test creating a namespace that already exists."""
        mock_client.create_namespace.side_effect = NamespaceAlreadyExistsException()

        result = runner.invoke(app, ["namespaces", "create", "existing_ns"])

        assert result.exit_code == 1
        assert "already exists" in result.stdout


@pytest.mark.unit
class TestNamespacesDelete:
    """Tests for 'kaizen namespaces delete' command."""

    def test_delete_namespace_not_found(self, mock_client):
        """Test deleting a namespace that doesn't exist."""
        mock_client.get_namespace_details.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["namespaces", "delete", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_delete_namespace_with_force(self, mock_client):
        """Test deleting a namespace with --force flag."""
        created_at = datetime.datetime.now(datetime.UTC)
        mock_client.get_namespace_details.return_value = Namespace(id="to_delete", created_at=created_at, num_entities=3)

        result = runner.invoke(app, ["namespaces", "delete", "to_delete", "--force"])

        assert result.exit_code == 0
        assert "Deleted namespace" in result.stdout
        mock_client.delete_namespace.assert_called_once_with("to_delete")

    def test_delete_namespace_cancelled(self, mock_client):
        """Test cancelling namespace deletion at confirmation prompt."""
        created_at = datetime.datetime.now(datetime.UTC)
        mock_client.get_namespace_details.return_value = Namespace(id="to_delete", created_at=created_at, num_entities=3)

        result = runner.invoke(app, ["namespaces", "delete", "to_delete"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.stdout
        mock_client.delete_namespace.assert_not_called()

    def test_delete_namespace_confirmed(self, mock_client):
        """Test confirming namespace deletion at prompt."""
        created_at = datetime.datetime.now(datetime.UTC)
        mock_client.get_namespace_details.return_value = Namespace(id="to_delete", created_at=created_at, num_entities=3)

        result = runner.invoke(app, ["namespaces", "delete", "to_delete"], input="y\n")

        assert result.exit_code == 0
        assert "Deleted namespace" in result.stdout
        mock_client.delete_namespace.assert_called_once_with("to_delete")


@pytest.mark.unit
class TestNamespacesInfo:
    """Tests for 'kaizen namespaces info' command."""

    def test_namespace_info_success(self, mock_client):
        """Test getting namespace info successfully."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.get_namespace_details.return_value = Namespace(id="my_namespace", created_at=created_at, num_entities=42)

        result = runner.invoke(app, ["namespaces", "info", "my_namespace"])

        assert result.exit_code == 0
        assert "my_namespace" in result.stdout
        assert "42" in result.stdout

    def test_namespace_info_not_found(self, mock_client):
        """Test getting info for non-existent namespace."""
        mock_client.get_namespace_details.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["namespaces", "info", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


# =============================================================================
# Entity Commands Tests
# =============================================================================


@pytest.mark.unit
class TestEntitiesList:
    """Tests for 'kaizen entities list' command."""

    def test_list_entities_empty(self, mock_client):
        """Test listing entities when none exist."""
        mock_client.get_all_entities.return_value = []

        result = runner.invoke(app, ["entities", "list", "my_namespace"])

        assert result.exit_code == 0
        assert "No entities found" in result.stdout

    def test_list_entities_with_results(self, mock_client):
        """Test listing entities with results."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.get_all_entities.return_value = [
            RecordedEntity(id="1", type="guideline", content="Always test your code", created_at=created_at),
            RecordedEntity(id="2", type="fact", content="Python is awesome", created_at=created_at),
        ]

        result = runner.invoke(app, ["entities", "list", "my_namespace"])

        assert result.exit_code == 0
        assert "guideline" in result.stdout
        assert "Always test your code" in result.stdout
        assert "fact" in result.stdout

    def test_list_entities_with_type_filter(self, mock_client):
        """Test listing entities with type filter."""
        mock_client.get_all_entities.return_value = []

        runner.invoke(app, ["entities", "list", "my_namespace", "--type", "guideline"])

        mock_client.get_all_entities.assert_called_once_with("my_namespace", filters={"type": "guideline"}, limit=100)

    def test_list_entities_namespace_not_found(self, mock_client):
        """Test listing entities from non-existent namespace."""
        mock_client.get_all_entities.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["entities", "list", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_list_entities_truncates_long_content(self, mock_client):
        """Test that long content is truncated in the list view."""
        created_at = datetime.datetime.now(datetime.UTC)
        long_content = "A" * 100  # Content longer than 60 chars
        mock_client.get_all_entities.return_value = [
            RecordedEntity(id="1", type="guideline", content=long_content, created_at=created_at),
        ]

        result = runner.invoke(app, ["entities", "list", "my_namespace"])

        assert result.exit_code == 0
        # Full content should not appear, but truncated version should
        assert long_content not in result.stdout
        # Rich uses ellipsis character (…) or three dots (...) for truncation
        assert "…" in result.stdout or "..." in result.stdout


@pytest.mark.unit
class TestEntitiesAdd:
    """Tests for 'kaizen entities add' command."""

    def test_add_entity_success(self, mock_client):
        """Test adding an entity successfully."""
        mock_client.namespace_exists.return_value = True
        mock_client.update_entities.return_value = [EntityUpdate(id="123", type="guideline", content="Test content", event="ADD")]

        result = runner.invoke(
            app,
            [
                "entities",
                "add",
                "my_namespace",
                "--content",
                "Test content",
                "--type",
                "guideline",
                "--no-conflict-resolution",
            ],
        )

        assert result.exit_code == 0
        assert "Entity ADD" in result.stdout
        assert "123" in result.stdout

    def test_add_entity_creates_namespace(self, mock_client):
        """Test that adding entity prompts to create namespace if it doesn't exist."""
        mock_client.namespace_exists.return_value = False
        mock_client.update_entities.return_value = [EntityUpdate(id="1", type="guideline", content="Test", event="ADD")]

        result = runner.invoke(
            app,
            [
                "entities",
                "add",
                "new_namespace",
                "--content",
                "Test content",
                "--no-conflict-resolution",
            ],
            input="y\n",
        )

        assert result.exit_code == 0
        mock_client.create_namespace.assert_called_once_with("new_namespace")

    def test_add_entity_declined_namespace_creation(self, mock_client):
        """Test declining namespace creation."""
        mock_client.namespace_exists.return_value = False

        result = runner.invoke(
            app,
            ["entities", "add", "new_namespace", "--content", "Test", "--no-conflict-resolution"],
            input="n\n",
        )

        assert result.exit_code == 1
        mock_client.create_namespace.assert_not_called()

    def test_add_entity_with_metadata(self, mock_client):
        """Test adding entity with JSON metadata."""
        mock_client.namespace_exists.return_value = True
        mock_client.update_entities.return_value = [EntityUpdate(id="1", type="guideline", content="Test", event="ADD")]

        result = runner.invoke(
            app,
            [
                "entities",
                "add",
                "my_namespace",
                "--content",
                "Test",
                "--metadata",
                '{"source": "test"}',
                "--no-conflict-resolution",
            ],
        )

        assert result.exit_code == 0
        # Verify metadata was passed to update_entities
        call_args = mock_client.update_entities.call_args
        entity = call_args[0][1][0]
        assert entity.metadata == {"source": "test"}

    def test_add_entity_invalid_metadata(self, mock_client):
        """Test adding entity with invalid JSON metadata."""
        mock_client.namespace_exists.return_value = True

        result = runner.invoke(
            app,
            [
                "entities",
                "add",
                "my_namespace",
                "--content",
                "Test",
                "--metadata",
                "not-valid-json",
                "--no-conflict-resolution",
            ],
        )

        assert result.exit_code == 1
        assert "Invalid JSON" in result.stdout

    def test_add_entity_no_results(self, mock_client):
        """Test adding entity when conflict resolution filters it out."""
        mock_client.namespace_exists.return_value = True
        mock_client.update_entities.return_value = []

        result = runner.invoke(
            app,
            ["entities", "add", "my_namespace", "--content", "Duplicate", "--no-conflict-resolution"],
        )

        assert result.exit_code == 0
        assert "filtered by conflict resolution" in result.stdout

    def test_add_entity_prompts_for_content(self, mock_client):
        """Test that CLI prompts for content if not provided."""
        mock_client.namespace_exists.return_value = True
        mock_client.update_entities.return_value = [EntityUpdate(id="1", type="guideline", content="Prompted content", event="ADD")]

        result = runner.invoke(
            app,
            ["entities", "add", "my_namespace", "--no-conflict-resolution"],
            input="Prompted content\n",
        )

        assert result.exit_code == 0
        call_args = mock_client.update_entities.call_args
        entity = call_args[0][1][0]
        assert entity.content == "Prompted content"


@pytest.mark.unit
class TestEntitiesDelete:
    """Tests for 'kaizen entities delete' command."""

    def test_delete_entity_success(self, mock_client):
        """Test deleting an entity successfully."""
        result = runner.invoke(app, ["entities", "delete", "my_namespace", "123"])

        assert result.exit_code == 0
        assert "Deleted entity" in result.stdout
        mock_client.delete_entity_by_id.assert_called_once_with("my_namespace", "123")

    def test_delete_entity_namespace_not_found(self, mock_client):
        """Test deleting entity from non-existent namespace."""
        mock_client.delete_entity_by_id.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["entities", "delete", "nonexistent", "123"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_delete_entity_error(self, mock_client):
        """Test error when deleting entity."""
        mock_client.delete_entity_by_id.side_effect = KaizenException("Delete failed")

        result = runner.invoke(app, ["entities", "delete", "my_namespace", "123"])

        assert result.exit_code == 1
        assert "Error deleting entity" in result.stdout


@pytest.mark.unit
class TestEntitiesSearch:
    """Tests for 'kaizen entities search' command."""

    def test_search_entities_no_results(self, mock_client):
        """Test searching entities with no results."""
        mock_client.search_entities.return_value = []

        result = runner.invoke(app, ["entities", "search", "my_namespace", "test query"])

        assert result.exit_code == 0
        assert "No matching entities found" in result.stdout

    def test_search_entities_with_results(self, mock_client):
        """Test searching entities with results."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.search_entities.return_value = [
            RecordedEntity(id="1", type="guideline", content="Matching content", created_at=created_at),
        ]

        result = runner.invoke(app, ["entities", "search", "my_namespace", "test query"])

        assert result.exit_code == 0
        assert "Matching content" in result.stdout
        assert "test query" in result.stdout

    def test_search_entities_with_type_filter(self, mock_client):
        """Test searching entities with type filter."""
        mock_client.search_entities.return_value = []

        runner.invoke(app, ["entities", "search", "my_namespace", "query", "--type", "guideline"])

        mock_client.search_entities.assert_called_once_with("my_namespace", query="query", filters={"type": "guideline"}, limit=10)

    def test_search_entities_namespace_not_found(self, mock_client):
        """Test searching in non-existent namespace."""
        mock_client.search_entities.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["entities", "search", "nonexistent", "query"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


@pytest.mark.unit
class TestEntitiesShow:
    """Tests for 'kaizen entities show' command."""

    def test_show_entity_success(self, mock_client):
        """Test showing entity details successfully."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.get_all_entities.return_value = [
            RecordedEntity(
                id="123",
                type="guideline",
                content="Full entity content here",
                created_at=created_at,
                metadata={"source": "test"},
            ),
        ]

        result = runner.invoke(app, ["entities", "show", "my_namespace", "123"])

        assert result.exit_code == 0
        assert "123" in result.stdout
        assert "guideline" in result.stdout
        assert "Full entity content here" in result.stdout
        assert "source" in result.stdout

    def test_show_entity_not_found(self, mock_client):
        """Test showing non-existent entity."""
        mock_client.get_all_entities.return_value = []

        result = runner.invoke(app, ["entities", "show", "my_namespace", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_show_entity_namespace_not_found(self, mock_client):
        """Test showing entity from non-existent namespace."""
        mock_client.get_all_entities.side_effect = NamespaceNotFoundException()

        result = runner.invoke(app, ["entities", "show", "nonexistent", "123"])

        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_show_entity_without_metadata(self, mock_client):
        """Test showing entity without metadata."""
        created_at = datetime.datetime(2024, 1, 15, 10, 30, 0, tzinfo=datetime.UTC)
        mock_client.get_all_entities.return_value = [
            RecordedEntity(id="123", type="guideline", content="Content without metadata", created_at=created_at),
        ]

        result = runner.invoke(app, ["entities", "show", "my_namespace", "123"])

        assert result.exit_code == 0
        assert "Content without metadata" in result.stdout
        # Metadata section should not appear
        assert "Metadata" not in result.stdout


# =============================================================================
# Help and Basic CLI Tests
# =============================================================================


@pytest.mark.unit
class TestCLIHelp:
    """Tests for CLI help commands."""

    def test_main_help(self):
        """Test main CLI help."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "Kaizen CLI" in result.stdout
        assert "namespaces" in result.stdout
        assert "entities" in result.stdout

    def test_namespaces_help(self):
        """Test namespaces subcommand help."""
        result = runner.invoke(app, ["namespaces", "--help"])

        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "create" in result.stdout
        assert "delete" in result.stdout
        assert "info" in result.stdout

    def test_entities_help(self):
        """Test entities subcommand help."""
        result = runner.invoke(app, ["entities", "--help"])

        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "add" in result.stdout
        assert "delete" in result.stdout
        assert "search" in result.stdout
        assert "show" in result.stdout

    def test_sync_help(self):
        """Test sync subcommand help."""
        result = runner.invoke(app, ["sync", "--help"])

        assert result.exit_code == 0
        assert "phoenix" in result.stdout


# =============================================================================
# Sync Commands Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.phoenix
class TestSyncPhoenix:
    """Tests for 'kaizen sync phoenix' command."""

    def test_sync_phoenix_default_params(self):
        """Test sync phoenix with default parameters."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=5, skipped=2, tips_generated=10, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix"])

            assert result.exit_code == 0
            mock_syncer.sync.assert_called_once_with(limit=100, include_errors=False)

    def test_sync_phoenix_with_custom_url(self):
        """Test sync phoenix with custom Phoenix URL."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://custom:8080"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix", "--url", "http://custom:8080"])

            assert result.exit_code == 0
            MockSync.assert_called_once_with(phoenix_url="http://custom:8080", namespace_id=None, project=None)

    def test_sync_phoenix_with_custom_namespace(self):
        """Test sync phoenix with custom namespace."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "my_namespace"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix", "--namespace", "my_namespace"])

            assert result.exit_code == 0
            MockSync.assert_called_once_with(phoenix_url=None, namespace_id="my_namespace", project=None)

    def test_sync_phoenix_with_custom_project(self):
        """Test sync phoenix with custom project."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "my_project"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix", "--project", "my_project"])

            assert result.exit_code == 0
            MockSync.assert_called_once_with(phoenix_url=None, namespace_id=None, project="my_project")

    def test_sync_phoenix_with_custom_limit(self):
        """Test sync phoenix with custom limit."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix", "--limit", "50"])

            assert result.exit_code == 0
            mock_syncer.sync.assert_called_once_with(limit=50, include_errors=False)

    def test_sync_phoenix_with_include_errors(self):
        """Test sync phoenix with include-errors flag."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix", "--include-errors"])

            assert result.exit_code == 0
            mock_syncer.sync.assert_called_once_with(limit=100, include_errors=True)

    def test_sync_phoenix_displays_results(self):
        """Test sync phoenix displays results in output."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(processed=10, skipped=5, tips_generated=20, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix"])

            assert result.exit_code == 0
            assert "Sync Results" in result.stdout
            assert "10" in result.stdout  # processed
            assert "5" in result.stdout  # skipped
            assert "20" in result.stdout  # tips_generated

    def test_sync_phoenix_displays_errors(self):
        """Test sync phoenix displays errors if any."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.return_value = MagicMock(
                processed=1, skipped=0, tips_generated=0, errors=["Error processing span abc: Connection failed"]
            )
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix"])

            assert result.exit_code == 0
            assert "Errors:" in result.stdout
            assert "Connection failed" in result.stdout

    def test_sync_phoenix_handles_exception(self):
        """Test sync phoenix handles exceptions gracefully."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://localhost:6006"
            mock_syncer.project = "default"
            mock_syncer.namespace_id = "test_ns"
            mock_syncer.sync.side_effect = Exception("Phoenix server unreachable")
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix"])

            assert result.exit_code == 1
            assert "Sync failed" in result.stdout
            assert "Phoenix server unreachable" in result.stdout

    def test_sync_phoenix_displays_parameters(self):
        """Test sync phoenix displays sync parameters."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://test:6006"
            mock_syncer.project = "test_project"
            mock_syncer.namespace_id = "test_namespace"
            mock_syncer.sync.return_value = MagicMock(processed=0, skipped=0, tips_generated=0, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(app, ["sync", "phoenix"])

            assert result.exit_code == 0
            assert "http://test:6006" in result.stdout
            assert "test_project" in result.stdout
            assert "test_namespace" in result.stdout

    def test_sync_phoenix_all_options(self):
        """Test sync phoenix with all options combined."""
        with patch("kaizen.sync.phoenix_sync.PhoenixSync") as MockSync:
            mock_syncer = MagicMock()
            mock_syncer.phoenix_url = "http://custom:9000"
            mock_syncer.project = "prod"
            mock_syncer.namespace_id = "production"
            mock_syncer.sync.return_value = MagicMock(processed=100, skipped=50, tips_generated=200, errors=[])
            MockSync.return_value = mock_syncer

            result = runner.invoke(
                app,
                [
                    "sync",
                    "phoenix",
                    "--url",
                    "http://custom:9000",
                    "--namespace",
                    "production",
                    "--project",
                    "prod",
                    "--limit",
                    "500",
                    "--include-errors",
                ],
            )

            assert result.exit_code == 0
            MockSync.assert_called_once_with(phoenix_url="http://custom:9000", namespace_id="production", project="prod")
            mock_syncer.sync.assert_called_once_with(limit=500, include_errors=True)
