"""ALTK-Evolve memory provider -- MemoryProvider interface (Phase 0, lite backend).

On-the-job learning: task guidelines are generated from session
trajectories and recalled per-turn via a structured, query-retrieved
format -- a middle tier between the char-capped built-in memory snapshot
and name-triggered skills (see ``README.md``).

Phase 0 ships the filesystem-only "lite" backend (``backend.LiteBackend``):
no server, no MCP client, no extra dependency. The provider itself
generates guidelines at capture time via ``agent.plugin_llm.PluginLlm``
(``guideline_gen.py``), using the user's active model + auth. Retrieval is
case-insensitive term-overlap scoring, not semantic search: semantic
retrieval needs a vector backend, which is server-only. An honest
limitation, not a bug.

Config via environment variables (see ``get_config_schema`` / README.md
for the full table):
  EVOLVE_MODE                    -- "lite" (default) or "server" (Phase 1 stub)
  EVOLVE_DIR                     -- storage root override
  EVOLVE_PREFETCH_LIMIT          -- max guidelines recalled per turn (default 5)
  EVOLVE_CAPTURE_EVERY_N_TURNS   -- periodic capture cadence (default 0 = off)
  EVOLVE_MIN_TURNS               -- min turns before session-end capture (default 2)
  EVOLVE_EXPOSE_TOOLS            -- expose evolve_* tools (default true)

Or via ``$HERMES_HOME/evolve/config.json`` (keys: mode, dir, prefetch_limit,
capture_every_n_turns, min_turns, expose_tools). Env vars win over the file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

from .backend import EvolveBackend, LiteBackend, ServerBackend, slugify
from .guideline_gen import generate_guidelines

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised indirectly; keep provider importable in isolation
    from agent.memory_manager import sanitize_context
except Exception:  # pragma: no cover

    def sanitize_context(text: str) -> str:
        return text


_PREFETCH_HEADER = "Guidelines learned from previous sessions (apply when relevant):"

_DEFAULT_PREFETCH_LIMIT = 5
_DEFAULT_CAPTURE_EVERY_N_TURNS = 0
_DEFAULT_MIN_TURNS = 2
_DEFAULT_EXPOSE_TOOLS = True


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

GET_GUIDELINES_SCHEMA = {
    "name": "evolve_get_guidelines",
    "description": (
        "Explicitly recall learned task guidelines relevant to a task. "
        "Guidelines are also injected automatically before each turn -- "
        "use this tool when you want to look up guidelines for a different "
        "task than the current one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Description of the task to find guidelines for."},
        },
        "required": ["task"],
    },
}

SAVE_GUIDELINE_SCHEMA = {
    "name": "evolve_save_guideline",
    "description": (
        "Save a durable, reusable guideline for future sessions -- a concrete "
        "solution, error-recovery step, or workaround. Do not include secrets, "
        "tokens, or one-off user-specific data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The guideline: a proactive statement of what TO DO."},
            "trigger": {"type": "string", "description": "The situational context when this guideline applies."},
            "rationale": {"type": "string", "description": "Why this approach works."},
        },
        "required": ["content"],
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
        return default
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_config(hermes_home: str) -> Dict[str, Any]:
    """Resolve config: env vars first, then ``$HERMES_HOME/evolve/config.json``."""
    file_cfg: Dict[str, Any] = {}
    config_path = Path(hermes_home) / "evolve" / "config.json"
    if config_path.exists():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                file_cfg = raw
        except Exception:
            logger.debug("evolve: failed to parse %s", config_path, exc_info=True)

    def _resolve(env_var: str, key: str, default: Any) -> Any:
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            return env_val
        file_val = file_cfg.get(key)
        if file_val not in (None, ""):
            return file_val
        return default

    mode = str(_resolve("EVOLVE_MODE", "mode", "lite")).strip().lower()
    if mode not in {"lite", "server"}:
        mode = "lite"

    return {
        "mode": mode,
        "dir": str(_resolve("EVOLVE_DIR", "dir", "")).strip(),
        "prefetch_limit": max(
            1, _as_int(_resolve("EVOLVE_PREFETCH_LIMIT", "prefetch_limit", _DEFAULT_PREFETCH_LIMIT), _DEFAULT_PREFETCH_LIMIT)
        ),
        "capture_every_n_turns": max(
            0,
            _as_int(
                _resolve("EVOLVE_CAPTURE_EVERY_N_TURNS", "capture_every_n_turns", _DEFAULT_CAPTURE_EVERY_N_TURNS),
                _DEFAULT_CAPTURE_EVERY_N_TURNS,
            ),
        ),
        "min_turns": max(0, _as_int(_resolve("EVOLVE_MIN_TURNS", "min_turns", _DEFAULT_MIN_TURNS), _DEFAULT_MIN_TURNS)),
        "expose_tools": _as_bool(_resolve("EVOLVE_EXPOSE_TOOLS", "expose_tools", _DEFAULT_EXPOSE_TOOLS), _DEFAULT_EXPOSE_TOOLS),
    }


def _save_config(values: Dict[str, Any], hermes_home: str) -> None:
    config_path = Path(hermes_home) / "evolve" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(values)
    from utils import atomic_json_write

    atomic_json_write(config_path, existing, mode=0o600, sort_keys=True)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_guidelines(entries: List[Dict[str, Any]]) -> str:
    """Render entities as a numbered guideline list under the recall header."""
    lines = [_PREFETCH_HEADER]
    for i, entry in enumerate(entries, 1):
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        trigger = (entry.get("trigger") or "").strip()
        lines.append(f"{i}. [{trigger}] {content}" if trigger else f"{i}. {content}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _entity_slug(entry: Dict[str, Any]) -> str:
    slug = entry.get("_slug")
    if slug:
        return str(slug)
    return slugify(entry.get("content", ""))


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------


class EvolveMemoryProvider(MemoryProvider):
    """ALTK-Evolve memory provider -- Phase 0 lite (filesystem) backend."""

    def __init__(self) -> None:
        self._hermes_home = ""
        self._session_id = ""
        self._agent_context = "primary"
        self._capture_allowed = True
        self._active = True

        self._mode = "lite"
        self._prefetch_limit = _DEFAULT_PREFETCH_LIMIT
        self._capture_every_n_turns = _DEFAULT_CAPTURE_EVERY_N_TURNS
        self._min_turns = _DEFAULT_MIN_TURNS
        self._expose_tools = _DEFAULT_EXPOSE_TOOLS

        self._backend: Optional[EvolveBackend] = None
        self._turn_count = 0
        self._last_messages: Optional[List[Dict[str, Any]]] = None

        self._prefetch_lock = threading.Lock()
        self._prefetch_cache: List[Dict[str, Any]] = []
        self._prefetch_thread: Optional[threading.Thread] = None
        self._capture_thread: Optional[threading.Thread] = None

    @property
    def name(self) -> str:
        return "evolve"

    def is_available(self) -> bool:
        # Lite mode needs no deps and makes no network calls -- always
        # available. Server mode (Phase 1) is gated inside initialize().
        return True

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "mode",
                "description": "Backend mode: 'lite' (filesystem, default) or 'server' (Phase 1, not yet implemented)",
                "default": "lite",
                "choices": ["lite", "server"],
            },
            {"key": "dir", "description": "Storage directory override (default: $HERMES_HOME/evolve)"},
            {"key": "prefetch_limit", "description": "Max guidelines recalled per turn", "default": "5"},
            {
                "key": "capture_every_n_turns",
                "description": "Capture a trajectory snapshot every N turns (0 = session-end only)",
                "default": "0",
            },
            {"key": "min_turns", "description": "Minimum turns before session-end capture fires", "default": "2"},
            {
                "key": "expose_tools",
                "description": "Expose evolve_get_guidelines / evolve_save_guideline tools",
                "default": "true",
                "choices": ["true", "false"],
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        _save_config(dict(values or {}), hermes_home)

    def initialize(self, session_id: str, **kwargs) -> None:
        try:
            from hermes_constants import get_hermes_home

            default_home = str(get_hermes_home())
        except Exception:
            default_home = str(Path.home() / ".hermes")

        self._hermes_home = kwargs.get("hermes_home") or default_home
        self._session_id = session_id
        self._turn_count = 0
        self._prefetch_cache = []

        self._agent_context = kwargs.get("agent_context") or "primary"
        self._capture_allowed = self._agent_context == "primary"

        cfg = _load_config(self._hermes_home)
        self._mode = cfg["mode"]
        self._prefetch_limit = cfg["prefetch_limit"]
        self._capture_every_n_turns = cfg["capture_every_n_turns"]
        self._min_turns = cfg["min_turns"]
        self._expose_tools = cfg["expose_tools"]

        store_root = Path(cfg["dir"]) if cfg["dir"] else Path(self._hermes_home) / "evolve"

        if self._mode == "server":
            # Phase 1 stub -- not functional yet. Disable rather than crash
            # on first use.
            logger.warning("evolve: EVOLVE_MODE=server is a Phase 1 stub; provider disabled")
            self._backend = ServerBackend()
            self._active = False
            return

        try:
            self._backend = LiteBackend(store_root, guideline_generator=generate_guidelines)
            self._active = True
        except Exception:
            logger.warning("evolve: failed to initialize lite backend", exc_info=True)
            self._backend = None
            self._active = False

    def system_prompt_block(self) -> str:
        if not self._active:
            return ""
        return (
            "# Evolve Memory\n"
            "Task guidelines learned from previous sessions may be injected under "
            '"Guidelines learned from previous sessions" -- apply them when relevant.\n'
            "Use evolve_save_guideline to record a durable, reusable lesson; "
            "evolve_get_guidelines to look up guidelines for a specific task on demand."
        )

    # -- recall -----------------------------------------------------------

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._active or not self._backend:
            return

        def _run() -> None:
            try:
                entries = self._backend.get_guidelines(query, self._prefetch_limit)
            except Exception:
                logger.debug("evolve: queue_prefetch failed", exc_info=True)
                entries = []
            with self._prefetch_lock:
                self._prefetch_cache = entries

        try:
            self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="evolve-prefetch")
            self._prefetch_thread.start()
        except Exception:
            logger.debug("evolve: failed to start prefetch thread", exc_info=True)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._active:
            return ""
        try:
            if self._prefetch_thread and self._prefetch_thread.is_alive():
                self._prefetch_thread.join(timeout=3.0)
            with self._prefetch_lock:
                entries = self._prefetch_cache
                self._prefetch_cache = []
            if not entries:
                return ""
            text = sanitize_context(_format_guidelines(entries))
            if not text.strip():
                return ""
            self._append_audit(session_id or self._session_id, entries)
            return text
        except Exception:
            logger.warning("evolve: prefetch failed", exc_info=True)
            return ""

    def _append_audit(self, session_id: str, entries: List[Dict[str, Any]]) -> None:
        """Append a recall event to $HERMES_HOME/evolve/audit.log.

        The row schema deliberately matches upstream evolve-lite's
        ``audit_recall.py`` (``event``/``session_id``/``entities``/``ts``, with
        ``entities`` as ``<type>/<name>`` ids relative to ``entities/``) so that
        Evolve's existing ``provenance`` skill can read Hermes sessions and
        judge whether a recalled guideline was followed, contradicted, or not
        applicable — without any Hermes-specific tooling.
        """
        try:
            audit_path = Path(self._hermes_home) / "evolve" / "audit.log"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "event": "recall",
                    "session_id": session_id,
                    "entities": [f"{(e.get('type') or 'guideline')}/{_entity_slug(e)}" for e in entries],
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                },
                ensure_ascii=False,
            )
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            logger.debug("evolve: failed to write audit log", exc_info=True)

    # -- capture ------------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not self._active:
            return
        try:
            self._turn_count += 1
            if messages:
                # Snapshot for flush-capture on a reset session switch —
                # gateway /new fires on_session_switch(reset=True), not
                # on_session_end (that only fires at agent shutdown).
                self._last_messages = list(messages)
            if self._capture_every_n_turns and self._capture_allowed and messages and self._turn_count % self._capture_every_n_turns == 0:
                self._capture_async(messages, session_id or self._session_id)
        except Exception:
            logger.warning("evolve: sync_turn failed", exc_info=True)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if not self._active or not self._capture_allowed:
            return
        try:
            self._last_messages = None  # end supersedes any pending flush
            turns = sum(1 for m in (messages or []) if isinstance(m, dict) and m.get("role") == "user")
            if turns < self._min_turns:
                return
            self._capture_async(messages, self._session_id)
        except Exception:
            logger.warning("evolve: on_session_end failed", exc_info=True)

    def _capture_async(self, messages: List[Dict[str, Any]], session_id: str) -> None:
        if not self._backend:
            return

        def _run() -> None:
            try:
                self._backend.save_trajectory(messages, session_id)
            except Exception:
                logger.warning("evolve: save_trajectory failed", exc_info=True)

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5.0)
        self._capture_thread = threading.Thread(target=_run, daemon=True, name="evolve-capture")
        self._capture_thread.start()

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        try:
            old_session_id = self._session_id
            if reset:
                # Gateway /new and CLI /reset arrive here, not at
                # on_session_end — flush-capture the finished session's
                # buffered transcript before dropping state.
                pending = self._last_messages
                self._last_messages = None
                if pending and self._capture_allowed:
                    turns = sum(1 for m in pending if isinstance(m, dict) and m.get("role") == "user")
                    if turns >= self._min_turns:
                        self._capture_async(pending, old_session_id)
                self._turn_count = 0
            self._session_id = new_session_id
            with self._prefetch_lock:
                self._prefetch_cache = []
        except Exception:
            logger.debug("evolve: on_session_switch failed", exc_info=True)

    # -- tools --------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        if not self._expose_tools or not self._active:
            return []
        return [GET_GUIDELINES_SCHEMA, SAVE_GUIDELINE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._active or not self._backend:
            return tool_error("Evolve memory provider is not active")
        try:
            if tool_name == "evolve_get_guidelines":
                return self._tool_get_guidelines(args)
            if tool_name == "evolve_save_guideline":
                return self._tool_save_guideline(args)
            return tool_error(f"Unknown tool: {tool_name}")
        except Exception as exc:
            logger.warning("evolve: tool call %s failed", tool_name, exc_info=True)
            return tool_error(f"evolve tool failed: {exc}")

    def _tool_get_guidelines(self, args: Dict[str, Any]) -> str:
        task = str(args.get("task") or "").strip()
        if not task:
            return tool_error("task is required")
        entries = self._backend.get_guidelines(task, self._prefetch_limit)
        return json.dumps(
            {
                "guidelines": [{"content": e.get("content", ""), "trigger": e.get("trigger", "")} for e in entries],
                "count": len(entries),
            }
        )

    def _tool_save_guideline(self, args: Dict[str, Any]) -> str:
        content = str(args.get("content") or "").strip()
        if not content:
            return tool_error("content is required")
        if not self._capture_allowed:
            return tool_error("Guideline capture is disabled for this session context")
        trigger = str(args.get("trigger") or "")
        rationale = str(args.get("rationale") or "")
        path = self._backend.save_guideline(content=content, trigger=trigger, rationale=rationale)
        return json.dumps({"saved": True, "path": path})

    # -- lifecycle ------------------------------------------------------------

    def shutdown(self) -> None:
        for attr_name in ("_prefetch_thread", "_capture_thread"):
            thread = getattr(self, attr_name, None)
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
            setattr(self, attr_name, None)


def register(ctx) -> None:
    """Register Evolve as a memory provider plugin."""
    ctx.register_memory_provider(EvolveMemoryProvider())
