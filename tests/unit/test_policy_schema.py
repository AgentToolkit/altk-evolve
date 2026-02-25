import json
import pytest
from kaizen.schema.policy import Policy, PolicyType, PolicyTrigger, TriggerType

pytestmark = pytest.mark.unit


def test_policy_serialization():
    """Test that a Policy object can be serialized to a dictionary/JSON."""
    trigger = PolicyTrigger(type=TriggerType.KEYWORD, value=["delete", "remove"], target="intent")

    policy = Policy(
        name="No Deletion",
        type=PolicyType.INTENT_GUARD,
        description="Prevents deletion operations",
        triggers=[trigger],
        content="Deletion is not allowed",
        config={"response_type": "text"},
    )

    # Serialize
    data = policy.model_dump()

    # Verify fields
    assert data["name"] == "No Deletion"
    assert data["type"] == "intent_guard"
    assert len(data["triggers"]) == 1
    assert data["triggers"][0]["type"] == "keyword"

    # Verify JSON serialization (simulating storage in Entity.content)
    json_str = policy.model_dump_json()
    loaded_data = json.loads(json_str)
    assert loaded_data["name"] == "No Deletion"


def test_policy_deserialization():
    """Test that a Policy object can be deserialized from a dictionary."""
    data = {
        "name": "Test Policy",
        "type": "playbook",
        "description": "A test playbook",
        "triggers": [{"type": "natural_language", "value": ["how to test"], "target": "intent", "threshold": 0.8}],
        "content": "# Test Playbook\n1. Step one",
        "priority": 10,
    }

    policy = Policy.model_validate(data)

    assert policy.name == "Test Policy"
    assert policy.type == PolicyType.PLAYBOOK
    assert len(policy.triggers) == 1
    assert policy.triggers[0].type == TriggerType.NATURAL_LANGUAGE
    assert policy.triggers[0].threshold == 0.8
    assert policy.priority == 10


def test_policy_trigger_defaults():
    """Test default values for PolicyTrigger."""
    trigger = PolicyTrigger(type=TriggerType.KEYWORD, value=["hello"])

    assert trigger.target == "intent"
    assert trigger.operator == "or"
    assert trigger.threshold == 0.7
