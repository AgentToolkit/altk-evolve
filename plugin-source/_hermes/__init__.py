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
  EVOLVE_SCOPE                   -- "global" (default), "user", or "chat"
  EVOLVE_PREFETCH_LIMIT          -- max guidelines recalled per turn (default 5)
  EVOLVE_CAPTURE_EVERY_N_TURNS   -- periodic capture cadence (default 0 = off)
  EVOLVE_MIN_TURNS               -- min turns before session-end capture (default 2)
  EVOLVE_EXPOSE_TOOLS            -- expose evolve_* tools (default true)

Or via ``$HERMES_HOME/evolve/config.json`` (keys: mode, dir, scope,
prefetch_limit, capture_every_n_turns, min_turns, expose_tools). Env vars win
over the file.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

from .backend import EvolveBackend, LiteBackend, ServerBackend, ensure_private_dir, slugify
from .guideline_gen import generate_guidelines

logger = logging.getLogger(__name__)

try:  # pragma: no cover - absent on Hermes builds that predate the recall indicator
    from agent.memory_provider import RecallStatus
except Exception:  # pragma: no cover
    RecallStatus = None  # type: ignore[assignment]


try:  # pragma: no cover - exercised indirectly; keep provider importable in isolation
    from agent.memory_manager import sanitize_context
except Exception:  # pragma: no cover

    def sanitize_context(text: str) -> str:
        return text


try:  # pragma: no cover - exercised via the injected screen; see _screen_stored_guideline
    from tools.threat_patterns import scan_for_threats

    _THREAT_SCAN_AVAILABLE = True
except Exception:  # pragma: no cover
    # WARNING, not a silent pass-through: without the host scanner *both* write
    # paths store unscanned model output, and a screen that finds nothing looks
    # exactly like a store with nothing wrong in it. The README says writes are
    # screened unconditionally, so the one state where that stops being true has
    # to be visible in the log rather than inferred from its absence.
    logger.warning("evolve: tools.threat_patterns unavailable -- stored guidelines will NOT be screened for threat patterns")

    _THREAT_SCAN_AVAILABLE = False

    def scan_for_threats(content: str, scope: str = "context") -> List[str]:
        return []


_PREFETCH_HEADER = "Guidelines learned from previous sessions (apply when relevant):"

_DEFAULT_PREFETCH_LIMIT = 5
_DEFAULT_CAPTURE_EVERY_N_TURNS = 0
_DEFAULT_MIN_TURNS = 2
_DEFAULT_EXPOSE_TOOLS = True
_DEFAULT_SCOPE = "global"
_SCOPES = ("global", "user", "chat")

# ``agent_context`` absent vs. present-and-"primary" are different situations:
# the first means the host never told us, the second that it did. A sentinel
# keeps them apart so the first can be logged.
_CONTEXT_UNSET = "<unset>"

# How long prefetch waits on a queued worker before doing the lookup itself.
_PREFETCH_JOIN_SECS = 3.0

# Longest guideline field the content screen will accept. Well under the host's
# MAX_SCAN_CHARS (65_536) so nothing we pass it is ever silently truncated --
# see _scan_strict for why the cap is a refusal rather than a chunked scan.
_MAX_FIELD_CHARS = 4000

# Reported like a threat-pattern id so one refusal path covers both reasons.
_OVERSIZED_FIELD = "oversized_field"


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

    scope = str(_resolve("EVOLVE_SCOPE", "scope", _DEFAULT_SCOPE)).strip().lower()
    if scope not in _SCOPES:
        scope = _DEFAULT_SCOPE

    return {
        "mode": mode,
        "scope": scope,
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
    ensure_private_dir(config_path.parent)
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


def _bucket_name(key: str) -> str:
    """Directory name for one scoped store — legible, but keyed on the digest.

    ``slugify`` is lossy by design: it folds every character outside
    ``[a-z0-9-]`` and truncates. So ``alice.b`` and ``alice/b`` both slug to
    ``alice-b``, and two ids sharing a 40-character prefix collide outright.
    An id with nothing sluggable in it — all punctuation, or entirely non-Latin
    — comes back as the literal ``entity``. Under ``EVOLVE_SCOPE`` any of these
    collisions merges two users' stores, which is the one outcome scoping exists
    to prevent. So the slug is decoration, there to keep the directory
    recognisable; a truncated SHA-256 of the *original* id is what identifies it.
    """
    return f"{slugify(key, max_length=40)}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


def _flatten(text: str) -> str:
    """Collapse a stored field to one escaped line, safe to inject.

    Two things happen here, both about the fact that guideline text is model
    output that was written to disk and is now going back into a prompt:

    - ``<`` and ``>`` are escaped, so stored content cannot spell a tag. The
      host's ``sanitize_context`` strips ``<memory-context>`` fences, but it is
      a single pass with no re-scan, so a nested or split fence survives it.
    - all whitespace collapses to single spaces, so one guideline cannot span
      lines and forge a header or a list item of its own. The numbered-list
      structure then belongs to this function alone.
    """
    return re.sub(r"\s+", " ", (text or "").strip()).replace("<", "&lt;").replace(">", "&gt;")


def _format_guidelines(entries: List[Dict[str, Any]]) -> str:
    """Render entities as a numbered guideline list under the recall header."""
    lines = [_PREFETCH_HEADER]
    for i, entry in enumerate(entries, 1):
        content = _flatten(entry.get("content") or "")
        if not content:
            continue
        trigger = _flatten(entry.get("trigger") or "")
        lines.append(f"{i}. [{trigger}] {content}" if trigger else f"{i}. {content}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _screen_candidates(content: str, trigger: str, rationale: str) -> List[str]:
    """Every string a stored guideline can turn into, for the threat screen.

    Scanning the fields newline-joined is not enough on its own. Most of the
    host's patterns are ``[^\\n]``-bounded, while ``_format_guidelines`` renders
    ``[trigger] content`` onto a *single* line — so a payload split across the
    two fields scans clean as separate lines and matches once recall joins them.
    A ``curl -X POST ... -d`` trigger with a ``"k=$OPENAI_API_KEY"`` content is
    the worked example: two innocent lines, one ``exfil_curl`` on the way in.

    So all three shapes are screened: the raw newline-joined fields (what the
    entity file holds), the same joined after ``_flatten`` (whitespace collapsed,
    tags escaped), and the rendered recall line itself.
    """
    fields = [p for p in (content, trigger, rationale) if p]
    flat_trigger, flat_content = _flatten(trigger), _flatten(content)
    rendered = f"[{flat_trigger}] {flat_content}" if flat_trigger else flat_content
    return ["\n".join(fields), "\n".join(_flatten(p) for p in fields), rendered]


def _scan_strict(content: str, trigger: str = "", rationale: str = "") -> List[str]:
    """Threat-pattern ids across every shape of a guideline. Raises through.

    ``scope="strict"`` — the set the host applies to *memory entries*, on write
    and again on load (``tools/memory_tool_store.py``). Its own comment gives
    the reason that applies here with more force than it does there: a memory
    entry enters the system prompt as a frozen snapshot and persists until
    explicitly removed, and a guideline is recalled into *every* later session.
    The host's other reason — that a flagged entry can be rewritten — does not
    hold for generated guidelines, since capture runs after the session ends.
    That asymmetry argues for the narrower ``context`` set, and it is the wrong
    trade: losing one generated guideline to a false positive costs a guideline,
    while storing an exfil instruction costs every session that recalls it.
    """
    if max(len(content), len(trigger), len(rationale)) > _MAX_FIELD_CHARS:
        # The host truncates at MAX_SCAN_CHARS (65_536) and returns findings only
        # from the head, so padding a field walks a payload past the scanner
        # entirely. Refusing is cheaper than chunk-scanning and keeps every
        # screened byte actually screened -- a guideline is a sentence or two by
        # design, so a field this long is already outside what capture produces.
        return [_OVERSIZED_FIELD]
    findings: Set[str] = set()
    for candidate in _screen_candidates(content, trigger, rationale):
        findings.update(scan_for_threats(candidate, scope="strict") or [])
    return sorted(findings)


def _screen_stored_guideline(content: str, trigger: str = "", rationale: str = "") -> List[str]:
    """``_scan_strict``, fail-open, for injection into ``LiteBackend``.

    Injected rather than imported there because ``backend.py`` stays
    stdlib-only. Screening on write rather than scrubbing on read is the point:
    a guideline that trips these patterns would be blocked on its way into the
    prompt anyway, so storing it only banks an entry that can never be used and
    hides the fact that generation produced something unusable.
    """
    try:
        return _scan_strict(content, trigger, rationale)
    except Exception:
        # WARNING, not DEBUG: this write went to disk unscreened, which is the
        # one outcome this function exists to prevent.
        logger.warning("evolve: threat scan failed; allowing the write", exc_info=True)
        return []


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
        self._user_id = ""
        self._chat_id = ""

        self._mode = "lite"
        self._scope = _DEFAULT_SCOPE
        self._prefetch_limit = _DEFAULT_PREFETCH_LIMIT
        self._capture_every_n_turns = _DEFAULT_CAPTURE_EVERY_N_TURNS
        self._min_turns = _DEFAULT_MIN_TURNS
        self._expose_tools = _DEFAULT_EXPOSE_TOOLS

        self._backend: Optional[EvolveBackend] = None
        self._turn_count = 0
        self._last_messages: Optional[List[Dict[str, Any]]] = None

        self._prefetch_lock = threading.Lock()
        # Tagged with the (session_id, query) it was fetched for: a result
        # scored against a different question, or against a session that has
        # since been replaced by /new, is not a cache hit. See prefetch().
        self._prefetch_cache: Optional[Tuple[str, str, List[Dict[str, Any]]]] = None
        self._prefetch_thread: Optional[threading.Thread] = None
        self._capture_thread: Optional[threading.Thread] = None

        # How many guidelines the LAST prefetch handed to the agent. Read by
        # recall_status() for the per-turn indicator, and reset at the top of
        # every prefetch so a turn that recalls nothing cannot report the
        # previous turn's count.
        self._last_recall_count = 0

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
            {
                "key": "scope",
                "description": "Partition the store: 'global' (one shared store), 'user', or 'chat'",
                "default": "global",
                "choices": list(_SCOPES),
            },
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
        self._prefetch_cache = None
        self._user_id = str(kwargs.get("user_id") or "")
        self._chat_id = str(kwargs.get("chat_id") or "")

        # Fail open when the host says nothing. Hermes passes agent_context on
        # every initialize (hard-coded "primary" for the main agent in
        # agent/agent_init.py), and honcho and supermemory both treat an absent
        # value as capture-allowed too. Failing closed here would silently stop
        # all capture the day Hermes stopped sending it — a quiet regression is
        # worse than the write we are gating. A value we *do* receive and do not
        # recognise is still refused.
        raw_context = kwargs.get("agent_context") or _CONTEXT_UNSET
        if raw_context == _CONTEXT_UNSET:
            logger.debug("evolve: no agent_context from the host; treating the session as primary")
        self._agent_context = "primary" if raw_context == _CONTEXT_UNSET else str(raw_context)
        self._capture_allowed = self._agent_context == "primary"

        cfg = _load_config(self._hermes_home)
        self._mode = cfg["mode"]
        self._scope = cfg["scope"]
        self._prefetch_limit = cfg["prefetch_limit"]
        self._capture_every_n_turns = cfg["capture_every_n_turns"]
        self._min_turns = cfg["min_turns"]
        self._expose_tools = cfg["expose_tools"]

        store_root = Path(cfg["dir"]) if cfg["dir"] else Path(self._hermes_home) / "evolve"
        store_root, unattributable = self._scoped_root(store_root)
        if unattributable:
            # Recall from the shared store still runs -- reading what is already
            # there harms nobody. Writing does not: an unattributable session
            # depositing into the shared store is exactly the leak EVOLVE_SCOPE
            # was set to prevent, and it would look partitioned while doing it.
            self._capture_allowed = False

        if self._mode == "server":
            # Phase 1 stub -- not functional yet. Disable rather than crash
            # on first use.
            logger.warning("evolve: EVOLVE_MODE=server is a Phase 1 stub; provider disabled")
            self._backend = ServerBackend()
            self._active = False
            return

        try:
            self._backend = LiteBackend(
                store_root,
                guideline_generator=generate_guidelines,
                content_screen=_screen_stored_guideline,
            )
            self._active = True
        except Exception:
            logger.warning("evolve: failed to initialize lite backend", exc_info=True)
            self._backend = None
            self._active = False

    def _scoped_root(self, root: Path) -> Tuple[Path, bool]:
        """Partition the store by user or chat when ``EVOLVE_SCOPE`` asks for it.

        ``global`` (the default) is a single shared store: guidelines are meant
        to be generalized procedures with no user-specific content, so sharing
        them is the design. That stops being true when strangers share one
        install — a Discord gateway, say — where anything one user's session
        produces is injected into everyone else's. ``user`` and ``chat`` give
        those deployments a partition without changing the default.

        Returns the store root and whether the session is *unattributable* — a
        scope was asked for but the host supplied no id for it. Reads still come
        from the shared root in that case; the caller refuses writes. Neither a
        shared "unknown" bucket nor a silent write to the shared root is safe:
        both pool exactly the users the scope exists to separate, behind a
        directory layout that looks partitioned.
        """
        if self._scope == "global":
            return root, False
        key = self._user_id if self._scope == "user" else self._chat_id
        if not key:
            logger.warning(
                "evolve: EVOLVE_SCOPE=%s but the host supplied no %s_id; "
                "recalling from the shared store and disabling capture for this session",
                self._scope,
                self._scope,
            )
            return root, True
        bucket = "users" if self._scope == "user" else "chats"
        return root / bucket / _bucket_name(key), False

    def _identity(self) -> Dict[str, Any]:
        """Attribution recorded with a captured trajectory."""
        return {"user_id": self._user_id, "chat_id": self._chat_id, "agent_context": self._agent_context}

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
        """Warm the cache for the next turn. Best-effort — ``prefetch`` can do it itself."""
        if not self._active or not self._backend:
            return
        tag = (session_id or self._session_id, query)

        def _run() -> None:
            try:
                entries = self._backend.get_guidelines(query, self._prefetch_limit)
            except Exception:
                logger.debug("evolve: queue_prefetch failed", exc_info=True)
                return
            with self._prefetch_lock:
                self._prefetch_cache = (tag[0], tag[1], entries)

        try:
            # copy_context so a profile HERMES_HOME override set in a contextvar
            # reaches the worker; a bare Thread would read the process default
            # and search the wrong store.
            self._prefetch_thread = threading.Thread(
                target=partial(contextvars.copy_context().run, _run),
                daemon=True,
                name="evolve-prefetch",
            )
            self._prefetch_thread.start()
        except Exception:
            logger.debug("evolve: failed to start prefetch thread", exc_info=True)

    def _take_cached(self, session_id: str, query: str) -> Optional[List[Dict[str, Any]]]:
        """Consume the queued result if it was fetched for this session and query."""
        with self._prefetch_lock:
            cached = self._prefetch_cache
            if cached is None:
                return None
            cached_session, cached_query, entries = cached
            if cached_session != session_id or cached_query != query:
                # Stale: a different question, or a session that /new replaced.
                # Drop it rather than inject guidelines scored against something
                # the user is no longer asking.
                self._prefetch_cache = None
                return None
            self._prefetch_cache = None
            return entries

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return the guideline block for *this* turn's query.

        The queued worker is only a cache. When it has nothing for this exact
        (session, query) — the first turn of a session, a turn after ``/new``, a
        worker that has not finished — the lookup happens here instead of
        silently serving the previous turn's guidelines or nothing at all.
        Retrieval is a filesystem scan of sub-kilobyte files, so doing it inline
        is cheap; the sibling mem0 provider recalls synchronously for the same
        reason.
        """
        # Reset before anything else: the indicator must describe THIS turn,
        # including on the early-return paths below.
        self._last_recall_count = 0
        if not self._active or not self._backend:
            return ""
        sid = session_id or self._session_id
        try:
            if self._prefetch_thread and self._prefetch_thread.is_alive():
                self._prefetch_thread.join(timeout=_PREFETCH_JOIN_SECS)
            entries = self._take_cached(sid, query)
            if entries is None:
                entries = self._backend.get_guidelines(query, self._prefetch_limit)
            if not entries:
                return ""
            text = sanitize_context(_format_guidelines(entries))
            if not text.strip():
                return ""
            self._append_audit(sid, entries)
            self._last_recall_count = len(entries)
            return text
        except Exception:
            logger.warning("evolve: prefetch failed", exc_info=True)
            return ""

    def recall_status(self) -> Optional["RecallStatus"]:
        """Describe what the last ``prefetch`` injected, for Hermes's indicator.

        Hermes calls this right after ``prefetch`` on the turn thread and renders
        ``🧠 Evolve — recalled 3 memories`` from it
        (``agent.memory_manager.describe_recall``), so the user sees that recall
        happened whether or not the model mentions it. Without this the automatic
        half of the loop is invisible: guidelines arrive in the context with
        nothing on screen to say so.

        Returns ``None`` when this turn injected nothing, and on Hermes builds
        that predate ``RecallStatus`` — an older host simply shows no indicator
        rather than failing to load the provider.
        """
        if RecallStatus is None or not self._last_recall_count:
            return None
        return RecallStatus(provider_label="Evolve", count=self._last_recall_count)

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
        identity = self._identity()

        def _run() -> None:
            try:
                self._backend.save_trajectory(messages, session_id, identity=identity)
            except Exception:
                logger.warning("evolve: save_trajectory failed", exc_info=True)

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5.0)
        # copy_context for the same reason as the prefetch worker: capture writes
        # to the store, and a profile HERMES_HOME override lives in a contextvar.
        self._capture_thread = threading.Thread(
            target=partial(contextvars.copy_context().run, _run),
            daemon=True,
            name="evolve-capture",
        )
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
            self._last_recall_count = 0
            with self._prefetch_lock:
                self._prefetch_cache = None
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
        # Unguarded, unlike the generated path's screen: a scanner that raises
        # here surfaces through handle_tool_call as a tool error the model can
        # retry, rather than a write nobody ever hears about.
        findings = _scan_strict(content, trigger, rationale)
        if findings:
            logger.warning("evolve: evolve_save_guideline refused (%s)", ", ".join(findings))
            return tool_error(f"Guideline refused by the content screen: {', '.join(findings)}. Rewrite it and try again.")
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
