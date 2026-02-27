"""Tests for conflict resolution functionality."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from kaizen.llm.conflict_resolution.conflict_resolution import (
    resolve_conflicts,
    get_update_entities_messages,
)
from kaizen.schema.conflict_resolution import SimpleEntity
from kaizen.schema.core import RecordedEntity


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_recorded_entities():
    """Create sample RecordedEntity objects for testing."""
    return [
        RecordedEntity(
            id="entity_1",
            type="guideline",
            content="Always use type hints in Python",
            metadata={"source": "code_review", "priority": "high"},
            created_at=datetime.now(),
        ),
        RecordedEntity(
            id="entity_2",
            type="guideline",
            content="Write unit tests for all functions",
            metadata={"source": "best_practices", "priority": "medium"},
            created_at=datetime.now(),
        ),
    ]


@pytest.fixture
def sample_new_recorded_entities():
    """Create sample new RecordedEntity objects for testing."""
    return [
        RecordedEntity(
            id="new_entity_1",
            type="guideline",
            content="Use descriptive variable names",
            metadata={"source": "code_review", "priority": "high"},
            created_at=datetime.now(),
        ),
    ]


@pytest.fixture
def mock_llm_response_add():
    """Mock LLM response for ADD operation."""
    return json.dumps(
        {
            "entities": [
                {
                    "id": "entity_1",
                    "type": "guideline",
                    "content": "Always use type hints in Python",
                    "event": "NONE",
                },
                {
                    "id": "entity_2",
                    "type": "guideline",
                    "content": "Write unit tests for all functions",
                    "event": "NONE",
                },
                {
                    "id": "new_entity_1",
                    "type": "guideline",
                    "content": "Use descriptive variable names",
                    "event": "ADD",
                },
            ]
        }
    )


@pytest.fixture
def mock_llm_response_update():
    """Mock LLM response for UPDATE operation."""
    return json.dumps(
        {
            "entities": [
                {
                    "id": "entity_1",
                    "type": "guideline",
                    "content": "Always use type hints and docstrings in Python",
                    "event": "UPDATE",
                    "old_entity": "Always use type hints in Python",
                },
                {
                    "id": "entity_2",
                    "type": "guideline",
                    "content": "Write unit tests for all functions",
                    "event": "NONE",
                },
            ]
        }
    )


@pytest.fixture
def mock_llm_response_delete():
    """Mock LLM response for DELETE operation."""
    return json.dumps(
        {
            "entities": [
                {
                    "id": "entity_1",
                    "type": "guideline",
                    "content": "Always use type hints in Python",
                    "event": "NONE",
                },
                {
                    "id": "entity_2",
                    "type": "guideline",
                    "content": "Write unit tests for all functions",
                    "event": "DELETE",
                },
            ]
        }
    )


@pytest.fixture
def mock_llm_response_with_markdown():
    """Mock LLM response wrapped in markdown code block."""
    return """```json
{
    "entities": [
        {
            "id": "entity_1",
            "type": "guideline",
            "content": "Test content",
            "event": "NONE"
        }
    ]
}
```"""


# =============================================================================
# SimpleEntity.from_recorded_entities() Tests
# =============================================================================


@pytest.mark.unit
def test_from_recorded_entities_basic(sample_recorded_entities):
    """Test basic conversion from RecordedEntity to SimpleEntity."""
    simple_entities = SimpleEntity.from_recorded_entities(sample_recorded_entities)

    assert len(simple_entities) == 2
    assert simple_entities[0].id == "entity_1"
    assert simple_entities[0].type == "guideline"
    assert simple_entities[0].content == "Always use type hints in Python"
    assert simple_entities[1].id == "entity_2"

    # Test conversion with empty list.
    simple_entities = SimpleEntity.from_recorded_entities([])
    assert simple_entities == []

    # Test that different content types are preserved.
    entities = [
        RecordedEntity(
            id="1",
            type="test",
            content="string content",
            metadata={},
            created_at=datetime.now(),
        ),
        RecordedEntity(
            id="2",
            type="test",
            content={"key": "value"},
            metadata={},
            created_at=datetime.now(),
        ),
        RecordedEntity(
            id="3",
            type="test",
            content=["item1", "item2"],
            metadata={},
            created_at=datetime.now(),
        ),
    ]

    simple_entities = SimpleEntity.from_recorded_entities(entities)

    assert isinstance(simple_entities[0].content, str)
    assert isinstance(simple_entities[1].content, dict)
    assert isinstance(simple_entities[2].content, list)


# =============================================================================
# get_update_entities_messages() Tests
# =============================================================================


@pytest.mark.unit
def test_get_update_entities_messages_default_prompt():
    """Test prompt generation with default template."""
    old_entities = [SimpleEntity(id="1", type="guideline", content="Old content")]
    new_entities = [SimpleEntity(id="2", type="guideline", content="New content")]

    prompt = get_update_entities_messages(old_entities, new_entities)

    assert "Old content" in prompt
    assert "New content" in prompt
    assert "ADD" in prompt
    assert "UPDATE" in prompt
    assert "DELETE" in prompt
    assert "NONE" in prompt
    assert '"id"' in prompt
    assert '"type"' in prompt
    assert '"content"' in prompt

    # Test prompt generation with custom template.
    custom_prompt = "Custom instructions for entity management"

    prompt = get_update_entities_messages(old_entities, new_entities, custom_prompt)

    assert "Custom instructions for entity management" in prompt
    assert "Old content" in prompt
    assert "New content" in prompt

    # Test prompt generation with empty old entities list.
    old_entities = []
    new_entities = [SimpleEntity(id="1", type="guideline", content="New content")]

    prompt = get_update_entities_messages(old_entities, new_entities)

    assert "Currently contains no entities" in prompt
    assert "New content" in prompt


# =============================================================================
# resolve_conflicts() Tests
# =============================================================================


@pytest.mark.unit
@patch("kaizen.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_event_types(
    mock_completion,
    sample_recorded_entities,
    sample_new_recorded_entities,
    mock_llm_response_add,
    mock_llm_response_update,
    mock_llm_response_delete,
):
    """Test successful conflict resolution with ADD, UPDATE, DELETE, and NONE operations."""
    # Test ADD operation
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = mock_llm_response_add
    mock_completion.return_value = mock_response

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_new_recorded_entities,
    )

    assert len(result) == 3
    assert result[0].event == "NONE"
    assert result[1].event == "NONE"
    assert result[2].event == "ADD"
    assert result[2].id == "new_entity_1"
    # Verify metadata was assigned for ADD operation
    assert result[2].metadata == {"source": "code_review", "priority": "high"}

    # Test UPDATE operation
    mock_response.choices[0].message.content = mock_llm_response_update
    mock_completion.return_value = mock_response

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_recorded_entities,
    )

    assert len(result) == 2
    assert result[0].event == "UPDATE"
    assert result[0].old_entity == "Always use type hints in Python"
    assert "docstrings" in result[0].content
    assert result[1].event == "NONE"
    # Verify UPDATE operation doesn't get metadata reassigned
    assert result[0].metadata == {}

    # Test DELETE operation
    mock_response.choices[0].message.content = mock_llm_response_delete
    mock_completion.return_value = mock_response

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_recorded_entities,
    )

    assert len(result) == 2
    assert result[0].event == "NONE"
    assert result[1].event == "DELETE"


@pytest.mark.unit
@patch("kaizen.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_response_parsing(
    mock_completion,
    sample_recorded_entities,
    mock_llm_response_with_markdown,
):
    """Test markdown cleaning and JSON parsing of LLM responses."""
    # Test that markdown code blocks are properly cleaned
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = mock_llm_response_with_markdown
    mock_completion.return_value = mock_response

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_recorded_entities,
    )

    assert len(result) == 1
    assert result[0].event == "NONE"

    # Test handling of malformed JSON response
    mock_response.choices[0].message.content = '{"entities": [invalid json}'
    mock_completion.return_value = mock_response

    with pytest.raises(Exception):
        resolve_conflicts(
            sample_recorded_entities,
            sample_recorded_entities,
        )

    # Test handling of response missing 'entities' key
    mock_response.choices[0].message.content = json.dumps({"wrong_key": []})
    mock_completion.return_value = mock_response

    with pytest.raises(Exception):
        resolve_conflicts(
            sample_recorded_entities,
            sample_recorded_entities,
        )


@pytest.mark.unit
@patch("kaizen.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_retry_logic(
    mock_completion,
    sample_recorded_entities,
    sample_new_recorded_entities,
    mock_llm_response_add,
):
    """Test retry logic when LLM calls fail."""
    # Test retry on JSON parsing error
    mock_response_fail = Mock()
    mock_response_fail.choices = [Mock()]
    mock_response_fail.choices[0].message.content = "invalid json"

    mock_response_success = Mock()
    mock_response_success.choices = [Mock()]
    mock_response_success.choices[0].message.content = mock_llm_response_add

    mock_completion.side_effect = [
        mock_response_fail,
        mock_response_fail,
        mock_response_success,
    ]

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_new_recorded_entities,
    )

    # Verify it succeeded after retries
    assert len(result) == 3
    assert mock_completion.call_count == 3

    # Test that exception is raised after max retries
    mock_completion.reset_mock()
    mock_completion.side_effect = Exception()

    with pytest.raises(Exception, match="Failed to resolve conflicts after 3 attempts"):
        resolve_conflicts(
            sample_recorded_entities,
            sample_recorded_entities,
        )

    # Verify it tried 3 times
    assert mock_completion.call_count == 3


@pytest.mark.unit
@patch("kaizen.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_edge_cases(
    mock_completion,
    sample_recorded_entities,
    sample_new_recorded_entities,
    mock_llm_response_add,
):
    """Test edge cases like empty lists and custom prompts."""
    # Test conflict resolution with empty entity lists
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = json.dumps({"entities": []})
    mock_completion.return_value = mock_response

    result = resolve_conflicts([], [])
    assert result == []

    # Test conflict resolution with custom prompt template
    mock_response.choices[0].message.content = mock_llm_response_add
    mock_completion.return_value = mock_response

    custom_prompt = "Custom conflict resolution instructions"

    result = resolve_conflicts(
        sample_recorded_entities,
        sample_new_recorded_entities,
        custom_update_entities_prompt=custom_prompt,
    )

    # Verify the call was made with custom prompt
    call_args = mock_completion.call_args
    assert custom_prompt in call_args[1]["messages"][0]["content"]
    assert len(result) == 3

    # Test that LLM settings are properly used
    assert "model" in call_args[1]
    assert "messages" in call_args[1]
    assert "custom_llm_provider" in call_args[1]
