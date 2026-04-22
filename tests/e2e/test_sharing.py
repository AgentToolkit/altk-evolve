"""
E2E tests for Phase 1B entity sharing: publish, unpublish, cross-namespace public recall.

Exercises the full MCP tool stack against real backends (filesystem + milvus-lite).
The `mcp` fixture is parameterized in conftest.py — every test here runs twice.
"""

import json
import uuid

import pytest
from fastmcp.client import Client


@pytest.mark.e2e
async def test_publish_entity_makes_it_retrievable_publicly(mcp):
    """publish_entity sets visibility=public; get_entities(include_public=True) finds it."""
    async with Client(transport=mcp) as client:
        # Create a private entity first
        create_resp = await client.call_tool_mcp(
            "create_entity",
            {"content": "always use type hints", "entity_type": "guideline", "enable_conflict_resolution": False},
        )
        entity = json.loads(create_resp.content[0].text)
        entity_id = entity["id"]

        # Publish it
        pub_resp = await client.call_tool_mcp("publish_entity", {"entity_id": entity_id, "user_id": "alice"})
        published = json.loads(pub_resp.content[0].text)
        assert published["metadata"]["visibility"] == "public"
        assert published["metadata"]["owner_id"] == "alice"
        assert "published_at" in published["metadata"]

        # Should appear in a public search
        get_resp = await client.call_tool_mcp(
            "get_entities",
            {"task": "type hints python", "entity_type": "guideline", "include_public": True},
        )
        output = get_resp.content[0].text
        assert "type hints" in output


@pytest.mark.e2e
async def test_unpublish_entity_removes_it_from_public_results(mcp):
    """unpublish_entity reverts visibility to private."""
    async with Client(transport=mcp) as client:
        create_resp = await client.call_tool_mcp(
            "create_entity",
            {"content": "prefer list comprehensions", "entity_type": "guideline", "enable_conflict_resolution": False},
        )
        entity_id = json.loads(create_resp.content[0].text)["id"]

        await client.call_tool_mcp("publish_entity", {"entity_id": entity_id})

        unpub_resp = await client.call_tool_mcp("unpublish_entity", {"entity_id": entity_id})
        unpublished = json.loads(unpub_resp.content[0].text)
        assert unpublished["metadata"]["visibility"] == "private"


@pytest.mark.e2e
async def test_publish_entity_not_found_returns_error(mcp):
    """publish_entity on a missing entity returns an error, not a 500."""
    async with Client(transport=mcp) as client:
        resp = await client.call_tool_mcp("publish_entity", {"entity_id": "99999"})
        result = json.loads(resp.content[0].text)
        assert "error" in result


@pytest.mark.e2e
async def test_create_entity_with_visibility_public(mcp):
    """create_entity(visibility=public) stores the visibility flag immediately."""
    async with Client(transport=mcp) as client:
        resp = await client.call_tool_mcp(
            "create_entity",
            {
                "content": "document all public functions",
                "entity_type": "guideline",
                "visibility": "public",
                "owner_id": "bob",
                "enable_conflict_resolution": False,
            },
        )
        result = json.loads(resp.content[0].text)
        assert result["metadata"]["visibility"] == "public"
        assert result["metadata"]["owner_id"] == "bob"


@pytest.mark.e2e
async def test_cross_namespace_public_discovery(mcp):
    """Entities published in a second namespace appear only when include_public=True."""
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    second_ns = f"test_x_{uuid.uuid4().hex[:6]}"
    second_client = EvolveClient()
    second_client.create_namespace(second_ns)

    try:
        second_client.update_entities(
            second_ns,
            [
                Entity(
                    type="guideline",
                    content="use dependency injection for testability",
                    metadata={"visibility": "public", "owner_id": "alice"},
                )
            ],
            enable_conflict_resolution=False,
        )

        async with Client(transport=mcp) as client:
            # With include_public=True the cross-namespace entity should surface
            with_public = await client.call_tool_mcp(
                "get_entities",
                {"task": "dependency injection", "entity_type": "guideline", "include_public": True},
            )
            assert "dependency injection" in with_public.content[0].text
            assert "[public: alice]" in with_public.content[0].text

            # Without include_public it must NOT appear (it lives in a different namespace)
            without_public = await client.call_tool_mcp(
                "get_entities",
                {"task": "dependency injection", "entity_type": "guideline", "include_public": False},
            )
            assert "dependency injection" not in without_public.content[0].text
    finally:
        try:
            second_client.delete_namespace(second_ns)
        except Exception:
            pass
        try:
            second_client.backend.close()
        except Exception:
            pass


@pytest.mark.e2e
async def test_patch_metadata_preserves_content(mcp):
    """publish_entity must not alter entity content — only metadata."""
    original_content = "avoid mutable default arguments"
    async with Client(transport=mcp) as client:
        create_resp = await client.call_tool_mcp(
            "create_entity",
            {"content": original_content, "entity_type": "guideline", "enable_conflict_resolution": False},
        )
        entity_id = json.loads(create_resp.content[0].text)["id"]

        pub_resp = await client.call_tool_mcp("publish_entity", {"entity_id": entity_id, "user_id": "carol"})
        published = json.loads(pub_resp.content[0].text)

        assert published["content"] == original_content
