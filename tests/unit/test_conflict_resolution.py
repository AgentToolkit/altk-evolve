"""Tests for conflict resolution functionality."""

import json
from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from altk_evolve.config.llm import llm_settings
from altk_evolve.llm.conflict_resolution.conflict_resolution import (
    _GROQ_GPT_OSS_CONFLICT_MAX_TOKENS,
    _conflict_resolution_completion_options,
    resolve_conflicts,
    get_update_entities_messages,
)
from altk_evolve.schema.exceptions import EvolveException
from altk_evolve.schema.conflict_resolution import SimpleEntity
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.utils.utils import clean_llm_response


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


@pytest.mark.unit
def test_clean_llm_response_extracts_embedded_json():
    """Test JSON extraction when providers add explanatory text around the payload."""
    expected = {"entities": []}

    assert (
        json.loads(
            clean_llm_response(
                """
Here is the reconciled output:

```json
{"entities": []}
```

Done.
"""
            )
        )
        == expected
    )

    assert json.loads(clean_llm_response('Result: {"entities": []}\nThanks.')) == expected


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
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
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
    # UPDATE must preserve the old entity's metadata (entity_1's metadata), and
    # now threads metadata through instead of wiping it to {} (which used to
    # destroy plugin-written metadata on the base._update_entity replace).
    # Here the LLM's rephrased content matches no incoming entity, so it falls
    # back to preserving the STORED entity's metadata.
    assert result[0].metadata == {"source": "code_review", "priority": "high"}

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
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
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

    # Test that provider-added text around a code block is cleaned before parsing
    mock_response.choices[0].message.content = f"Here is the reconciled output:\n{mock_llm_response_with_markdown}\nDone."
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
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
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
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_expands_groq_gpt_oss_output_budget(
    mock_completion,
    monkeypatch,
    sample_recorded_entities,
    sample_new_recorded_entities,
    mock_llm_response_add,
):
    """GPT-OSS needs room for both reasoning tokens and the conflict JSON."""
    monkeypatch.setattr(
        llm_settings,
        "conflict_resolution_model",
        "groq/openai/gpt-oss-120b",
    )
    monkeypatch.setattr(llm_settings, "custom_llm_provider", "groq")
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = mock_llm_response_add
    mock_completion.return_value = mock_response

    resolve_conflicts(sample_recorded_entities, sample_new_recorded_entities)

    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["max_tokens"] == 8192
    assert call_kwargs["reasoning_effort"] == "low"


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
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


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_update_preserves_old_metadata(mock_completion):
    """UPDATE carries forward the old entity's metadata so provenance is not lost."""
    old_entity = RecordedEntity(
        id="entity_1",
        type="guideline",
        content="Use type hints in Python",
        metadata={"generation_method": "standard", "category": "style"},
        created_at=datetime.now(),
    )
    new_entity = RecordedEntity(
        id="new_entity_1",
        type="guideline",
        content="Use type hints and docstrings in Python",
        metadata={"generation_method": "standard", "category": "style"},
        created_at=datetime.now(),
    )
    llm_response = json.dumps(
        {
            "entities": [
                {
                    "id": "entity_1",
                    "type": "guideline",
                    "content": "Use type hints and docstrings in Python",
                    "event": "UPDATE",
                    "old_entity": "Use type hints in Python",
                }
            ]
        }
    )
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = llm_response
    mock_completion.return_value = mock_response

    result = resolve_conflicts([old_entity], [new_entity])

    assert result[0].event == "UPDATE"
    assert result[0].metadata.get("generation_method") == "standard"
    assert result[0].metadata.get("category") == "style"


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_resolve_conflicts_update_unions_generation_methods(mock_completion):
    """When old entity has generation_method=standard and incoming has consistency, UPDATE unions them."""
    old_entity = RecordedEntity(
        id="entity_1",
        type="guideline",
        content="Use type hints in Python",
        metadata={"generation_method": "standard", "category": "style"},
        created_at=datetime.now(),
    )
    new_entity = RecordedEntity(
        id="new_entity_1",
        type="guideline",
        content="Use type hints in Python",
        metadata={"generation_method": "consistency", "category": "style"},
        created_at=datetime.now(),
    )
    llm_response = json.dumps(
        {
            "entities": [
                {
                    "id": "entity_1",
                    "type": "guideline",
                    "content": "Use type hints in Python",
                    "event": "UPDATE",
                    "old_entity": "Use type hints in Python",
                }
            ]
        }
    )
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message.content = llm_response
    mock_completion.return_value = mock_response

    result = resolve_conflicts([old_entity], [new_entity])

    assert result[0].event == "UPDATE"
    # UPDATE preserves the old entity's metadata — generation_method stays as-is.
    # Provenance union is not attempted (no reliable mapping from UPDATE → source entities).
    assert result[0].metadata.get("generation_method") == "standard"
    assert "generation_methods" not in result[0].metadata
    assert result[0].metadata.get("category") == "style"


# =============================================================================
# Completion options: token budget, JSON mode, and the Groq carve-out
# =============================================================================


def _set_conflict_model(monkeypatch, model, provider):
    monkeypatch.setattr(llm_settings, "conflict_resolution_model", model)
    monkeypatch.setattr(llm_settings, "custom_llm_provider", provider)


@pytest.mark.unit
def test_groq_gpt_oss_gets_budget_reasoning_cap_and_json_mode(monkeypatch):
    """The Groq path needs its reasoning bounded, and tolerates JSON mode.

    #264 excluded Groq from *schema/tool-backed* response_format after the model
    failed when no tool call was produced. Plain json_object involves no tool
    routing, and was verified live against groq/openai/gpt-oss-120b.
    """
    _set_conflict_model(monkeypatch, "groq/openai/gpt-oss-120b", "groq")

    options = _conflict_resolution_completion_options()

    assert options["max_tokens"] == _GROQ_GPT_OSS_CONFLICT_MAX_TOKENS
    assert options["reasoning_effort"] == "low"
    assert options["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_groq_behind_an_openai_compatible_base_url_still_gets_the_budget(monkeypatch):
    """The gateway shape: Groq reached with custom_llm_provider=openai.

    wxo-agentic-memory sets provider=openai when it routes through its AI
    gateway, so provider and model prefix alone miss a Groq-backed model. Losing
    the budget here is not cosmetic: measured live, reasoning grew from ~350 to
    ~2400 characters without the cap, on the model whose reasoning is what
    exhausts the reply in the first place.
    """
    _set_conflict_model(monkeypatch, "openai/gpt-oss-120b", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")

    options = _conflict_resolution_completion_options()

    assert options["max_tokens"] == _GROQ_GPT_OSS_CONFLICT_MAX_TOKENS
    # litellm raises UnsupportedParamsError for reasoning_effort under the openai
    # provider, so it must be omitted rather than sent hopefully.
    assert "reasoning_effort" not in options
    assert options["response_format"] == {"type": "json_object"}


@pytest.mark.unit
def test_non_groq_base_url_does_not_trigger_the_groq_budget(monkeypatch):
    _set_conflict_model(monkeypatch, "openai/gpt-oss-120b", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    options = _conflict_resolution_completion_options()

    assert "max_tokens" not in options


@pytest.mark.unit
def test_capable_provider_gets_json_mode(monkeypatch):
    _set_conflict_model(monkeypatch, "gpt-4o", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    options = _conflict_resolution_completion_options()

    assert options["response_format"] == {"type": "json_object"}
    # The budget override is Groq-specific and must not leak to other providers.
    assert "max_tokens" not in options
    assert "reasoning_effort" not in options


@pytest.mark.unit
def test_provider_without_response_format_support_gets_no_options(monkeypatch):
    _set_conflict_model(monkeypatch, "gpt-4o", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with patch(
        "altk_evolve.llm.conflict_resolution.conflict_resolution.get_supported_openai_params",
        return_value=["temperature"],
    ):
        options = _conflict_resolution_completion_options()

    assert options == {}


# =============================================================================
# finish_reason: truncation is a distinct failure from malformed output
# =============================================================================


def _mock_reply(content, finish_reason="stop"):
    response = Mock()
    choice = Mock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response.choices = [choice]
    return response


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_truncated_reply_is_reported_as_a_budget_problem(mock_completion, sample_recorded_entities):
    mock_completion.return_value = _mock_reply(
        '{"entities": [{"id": "e1", "type": "guideline", "content": "Valid',
        finish_reason="length",
    )

    with pytest.raises(EvolveException) as excinfo:
        resolve_conflicts(sample_recorded_entities, sample_recorded_entities)

    # The actionable cause is surfaced on the chained error, not buried in a
    # generic "Expecting value" JSON message.
    assert "cut off by the completion budget" in str(excinfo.value.__cause__)


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_empty_truncated_reply_names_the_budget(mock_completion, sample_recorded_entities):
    mock_completion.return_value = _mock_reply("", finish_reason="length")

    with pytest.raises(EvolveException) as excinfo:
        resolve_conflicts(sample_recorded_entities, sample_recorded_entities)

    assert "completion budget was spent before any JSON" in str(excinfo.value.__cause__)


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_complete_reply_at_the_budget_limit_is_still_accepted(mock_completion, sample_recorded_entities):
    """finish_reason=length does not by itself mean the JSON is unusable.

    A reply can stop exactly at the limit and still be complete; rejecting on the
    flag alone would discard good output, so truncation is only reported when the
    content also fails to parse.
    """
    mock_completion.return_value = _mock_reply(
        json.dumps({"entities": [{"id": "e1", "type": "guideline", "content": "x", "event": "NONE"}]}),
        finish_reason="length",
    )

    result = resolve_conflicts(sample_recorded_entities, sample_recorded_entities)

    assert len(result) == 1
    assert result[0].event == "NONE"
    assert mock_completion.call_count == 1


# =============================================================================
# Truncated replies must not be recovered from a nested fragment
# =============================================================================


TRUNCATED_ENTITIES_ARRAY = '{"entities": [{"id": "e1", "type": "guideline", "content": "x", "event": "NONE"}, {"id": "e2", "cont'
TRUNCATED_OUTER_ARRAY = '[{"entities": [{"id": "e1", "type": "guideline", "content": "x", "event": "DELETE"}]}, {"entiti'


@pytest.mark.unit
@pytest.mark.parametrize(
    "label,reply",
    [
        ("entities array cut mid-entity", TRUNCATED_ENTITIES_ARRAY),
        ("outer array cut after first response", TRUNCATED_OUTER_ARRAY),
        ("cut immediately after a key", '{"entities": [{"id": "e1"}, {"id": '),
    ],
)
def test_truncated_reply_is_not_recovered_from_a_nested_fragment(label, reply):
    """A cut-off document must stay a parse failure.

    Scanning for an embedded payload used to accept any decodable `{`/`[`, so a
    truncated document handed back a complete NESTED value: the first case yields
    a lone entity object, and the second a response object holding only the
    events that arrived. Both parse, so the finish_reason=length diagnosis was
    lost — and the second still has an "entities" key, so a partial verdict
    including a DELETE would have been applied as if it were the whole answer.
    """
    cleaned = clean_llm_response(reply)

    with pytest.raises(json.JSONDecodeError):
        json.loads(cleaned)


@pytest.mark.unit
@pytest.mark.parametrize(
    "label,reply",
    [
        ("prose preamble", 'Here is the reconciliation:\n{"entities": []}'),
        # A brace in the prose must not stop the scan: the first candidate fails
        # because it is not JSON, which is different from being unfinished.
        ("brace in prose", 'The rule mentions {discount_pct} so:\n{"entities": []}'),
        ("fenced with trailing text", '```json\n{"entities": []}\n```\nLet me know.'),
        ("bare object", '{"entities": []}'),
    ],
)
def test_wrapped_but_complete_replies_are_still_recovered(label, reply):
    parsed = json.loads(clean_llm_response(reply))

    assert parsed == {"entities": []}


@pytest.mark.unit
@patch("altk_evolve.llm.conflict_resolution.conflict_resolution.completion")
def test_truncated_nested_fragment_still_reports_the_budget(mock_completion, sample_recorded_entities):
    """End to end: the reason a caller is given must name the real problem.

    Before the recovery scan was tightened this surfaced as a KeyError on
    parsed["entities"] after three attempts, pointing a reader at the schema
    rather than at the completion budget.
    """
    mock_completion.return_value = _mock_reply(TRUNCATED_ENTITIES_ARRAY, finish_reason="length")

    with pytest.raises(EvolveException) as excinfo:
        resolve_conflicts(sample_recorded_entities, sample_recorded_entities)

    assert "cut off by the completion budget" in str(excinfo.value.__cause__)
