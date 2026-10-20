"""Unit tests for the REST create-entity route's guideline validation."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from altk_evolve.frontend.api.routes import EntityCreateRequest, create_namespace_entity


def _create_guideline(category: str, **extra):
    """Post a structured guideline and return the entity the route handed to the store."""
    client = MagicMock()
    client.update_entities.return_value = [MagicMock(id="1")]
    req = EntityCreateRequest(
        type="guideline",
        content="Validate files before parsing",
        metadata={"rationale": "Malformed input fails late", "category": category, "trigger": "Before parsing", **extra},
    )
    with patch("altk_evolve.frontend.mcp.mcp_server.get_client", return_value=client):
        create_namespace_entity("ns", req)
    (_, [entity]), _ = client.update_entities.call_args
    return entity


@pytest.mark.unit
@pytest.mark.parametrize(("posted", "stored"), [("strategy|recovery", "strategy"), ("Strategy", "strategy"), ("recovery", "recovery")])
def test_stores_the_category_the_gate_accepted(posted, stored):
    """The gate repairs a recoverable category, so the repaired value is what must be stored.

    Storing the raw one would let the route persist exactly the malformed label the repair
    exists for, where before this gate rejected it with a 422.
    """
    assert _create_guideline(posted).metadata["category"] == stored


@pytest.mark.unit
def test_rejects_an_invented_category():
    with pytest.raises(HTTPException) as exc:
        _create_guideline("made-up")
    assert exc.value.status_code == 422


@pytest.mark.unit
def test_drops_merged_from():
    # Consolidation's record of absorbed originals; a forged one could hide or delete a guideline.
    entity = _create_guideline("strategy", merged_from=[{"id": "1", "content_sha256": "0" * 64}])
    assert "merged_from" not in entity.metadata
    assert entity.metadata["rationale"] == "Malformed input fails late"
