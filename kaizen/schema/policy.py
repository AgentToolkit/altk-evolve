from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class PolicyType(str, Enum):
    PLAYBOOK = "playbook"
    INTENT_GUARD = "intent_guard"
    TOOL_GUIDE = "tool_guide"
    TOOL_APPROVAL = "tool_approval"
    OUTPUT_FORMATTER = "output_formatter"


class TriggerType(str, Enum):
    KEYWORD = "keyword"
    NATURAL_LANGUAGE = "natural_language"
    ALWAYS = "always"
    # App, State, and Tool triggers can be added here if needed in the future


class PolicyTrigger(BaseModel):
    type: TriggerType
    value: Optional[List[str]] = None
    target: str = "intent"
    operator: str = "or"  # "and" / "or" for keywords
    threshold: float = 0.7  # for natural_language triggers


class Policy(BaseModel):
    id: Optional[str] = None
    name: str
    type: PolicyType
    description: str
    triggers: List[PolicyTrigger]
    content: str  # The policy payload (playbook markdown, response text, etc.)
    config: Dict[str, Any] = Field(default_factory=dict)  # Type-specific config
    priority: int = 50
    enabled: bool = True
