"""Stub of hermes-agent's ``agent/memory_provider.py`` base class.

Deliberately NOT an ABC: instantiating the real provider must not require
implementing members these tests do not exercise. The real base class defines
the recall/capture hooks Hermes calls; the provider overrides the ones it uses,
and the tests call those overrides directly.

What the methods below *are* for is the signatures. An empty ``class
MemoryProvider: pass`` lets a provider override drift from the host contract
without anything failing: rename ``session_id`` to ``sid``, drop the
keyword-only marker on ``prefetch``, or make ``reset`` positional, and every
test here keeps passing while Hermes breaks on the first call. So each hook the
provider overrides is declared here with the host's exact signature, copied from
hermes-agent ``agent/memory_provider.py`` (``a792d0794f``), and
``test_hermes.py::TestHermesProviderContract`` compares the two with
``inspect.signature``.

Bodies are no-ops returning the declared empty value — nothing inherits
behaviour from here, only shape.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

INDICATOR_GLYPH = "🧠"


@dataclass(frozen=True)
class RecallStatus:
    """Shape of the host's per-turn recall indicator payload.

    Frozen and field-for-field identical to the real one, so a provider that
    constructs it with the wrong field names or a positional argument the host
    does not accept fails here rather than in a live session.
    """

    provider_label: str
    count: int
    glyph: str = INDICATOR_GLYPH


class MemoryProvider:
    pre_compress_checkpoint_api_version = 1

    @property
    def name(self) -> str:
        return ""

    def is_available(self) -> bool:
        return False

    def initialize(self, session_id: str, **kwargs) -> None:
        return None

    def unavailable_reason(self) -> str:
        return ""

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        return None

    def recall_status(self) -> Optional[RecallStatus]:
        return None

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        return ""

    def shutdown(self) -> None:
        return None

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        return None

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        return None

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        return None

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        return None

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        return None

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        return None

    def backup_paths(self) -> List[str]:
        return []
