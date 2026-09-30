"""Backend seam for the Evolve memory provider.

The provider (``__init__.py``, next to this file) talks to a 3-method
interface — ``get_guidelines``, ``save_guideline``, ``save_trajectory`` —
with two implementations:

- ``LiteBackend`` (Phase 0): markdown entities on the local filesystem,
  case-insensitive term-overlap retrieval, in-plugin guideline generation.
  No server, no network, no extra dependency.
- ``ServerBackend`` (Phase 1): MCP client to a running ``evolve-mcp``
  server. Not implemented in Phase 0 — every method raises
  ``NotImplementedError`` so a misconfigured ``EVOLVE_MODE=server`` fails
  loudly instead of silently doing nothing.

Entity files are markdown under ``entities/{type}/{slug}.md`` with YAML
frontmatter and an optional ``## Rationale`` section. That format is owned
by the shared evolve-lite ``entity_io`` module, which this bundle imports —
see the import block below.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# The shared entity_io ships into this bundle at lib/evolve-lite/entity_io.py
# (rendered from plugin-source/lib/ by build_plugins.py). Importing it keeps one
# source of truth for the on-disk format, and costs no pip dependency: it is
# stdlib-only and travels with the plugin.
_LIB_DIR = Path(__file__).resolve().parent / "lib" / "evolve-lite"


def _load_bundled(module_name: str) -> Any:
    """Import a stdlib-only module out of the bundled evolve-lite lib.

    Loaded by explicit path rather than by prepending ``_LIB_DIR`` to
    ``sys.path``, and registered under a namespaced ``evolve_lite_*`` key: this
    runs inside a long-lived host process, so the bundle must add nothing to the
    import namespace that unrelated code could pick up by accident. A bare
    ``entity_io`` is generic enough to collide, and the other prompt-driven
    integrations do put this directory on ``sys.path`` — where it captures every
    module name in it, ``config`` included.
    """
    path = _LIB_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"evolve_lite_{module_name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load bundled evolve-lite module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


_entity_io = _load_bundled("entity_io")

entity_to_markdown = _entity_io.entity_to_markdown
markdown_to_entity = _entity_io.markdown_to_entity
_slugify = _entity_io.slugify
_sanitize_type = _entity_io.sanitize_type
_write_entity_file = _entity_io.write_entity_file

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "of",
    "in",
    "on",
    "for",
    "is",
    "are",
    "with",
    "this",
    "that",
    "it",
    "be",
    "as",
    "at",
    "by",
    "from",
    "was",
    "were",
    "will",
    "would",
    "should",
    "can",
    "could",
    "not",
}
# ``[^\W_]+`` rather than ``[a-z0-9]+``: word characters minus the underscore,
# which keeps accented and non-Latin words instead of shredding them into
# nothing. A guideline written in French or Japanese was previously unreachable
# by a query in the same language — every token dropped on both sides, so the
# overlap was always zero.
_WORD_RE = re.compile(r"[^\W_]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens, stopwords and single-char tokens dropped."""
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 1]


def ensure_private_dir(path: Path) -> Path:
    """Create *path* (and parents) owner-only, leaving an existing dir's mode alone.

    The store holds session-derived content, so a directory this code creates
    should not be group- or world-readable. ``mkdir(mode=...)`` is masked by the
    process umask — set by a host we do not control — so the mode is applied
    with an explicit ``chmod``.

    Only on creation, though: a directory that already exists may have been
    given a deliberate mode (an ``EVOLVE_DIR`` store shared between accounts,
    say), and silently narrowing it would be a surprise. A ``chmod`` failure is
    not fatal either — a readable store beats a provider that cannot write.
    """
    existed = path.is_dir()
    path.mkdir(parents=True, exist_ok=True)
    if not existed:
        try:
            path.chmod(0o700)
        except OSError:
            logger.debug("evolve: could not tighten permissions on %s", path, exc_info=True)
    return path


def make_private(path: Path) -> None:
    """Make an existing file owner-only. Never raises — see ``ensure_private_dir``."""
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("evolve: could not tighten permissions on %s", path, exc_info=True)


# ---------------------------------------------------------------------------
# Entity file format — thin wrappers over the shared entity_io
# ---------------------------------------------------------------------------


def slugify(text: str, max_length: int = 60) -> str:
    """Slugify *text*, tolerating ``None``.

    The shared implementation assumes a string; provider callers pass values
    straight from LLM output, which may be missing.
    """
    return _slugify(text or "", max_length=max_length)


def write_entity_file(directory: Any, entity: Dict[str, Any], filename: Optional[str] = None) -> Path:
    """Write an entity as markdown under ``directory/{type}/{slug}.md``.

    The entity is copied first: the shared implementation stamps the sanitized
    ``type`` onto the dict it is handed, and callers here reuse their dicts.
    ``overwrite=False`` keeps the historical behaviour of suffixing ``-2``,
    ``-3``, … on slug collision rather than replacing an existing entity.
    """
    return _write_entity_file(directory, dict(entity), filename=filename, overwrite=False)


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------


class EvolveBackend:
    """Abstract backend interface consumed by ``EvolveMemoryProvider``."""

    def get_guidelines(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Return up to *limit* guideline entity dicts relevant to *query*."""
        raise NotImplementedError

    def save_guideline(self, content: str, trigger: str = "", rationale: str = "", type: str = "guideline") -> str:
        """Persist a guideline; return an id/path identifying it."""
        raise NotImplementedError

    def save_trajectory(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        *,
        identity: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a trajectory and (if configured) generate + save guidelines from it.

        ``identity`` carries optional attribution fields (``user_id``,
        ``chat_id``) recorded alongside the trajectory.

        Returns a summary dict, e.g. ``{"trajectory_path": ..., "guidelines": [...]}``.
        """
        raise NotImplementedError


class ServerBackend(EvolveBackend):
    """Phase 1 stub — MCP client to a running ``evolve-mcp`` server.

    Not implemented in Phase 0. Every method raises ``NotImplementedError``
    so ``EVOLVE_MODE=server`` fails loudly rather than silently no-op'ing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_guidelines(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            'ServerBackend is a Phase 1 stub (see README.md, "Lite vs. server mode"). '
            "Set EVOLVE_MODE=lite (the default) to use the filesystem backend."
        )

    def save_guideline(self, content: str, trigger: str = "", rationale: str = "", type: str = "guideline") -> str:
        raise NotImplementedError("ServerBackend is a Phase 1 stub; use EVOLVE_MODE=lite.")

    def save_trajectory(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        *,
        identity: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("ServerBackend is a Phase 1 stub; use EVOLVE_MODE=lite.")


class LiteBackend(EvolveBackend):
    """Phase 0 filesystem backend.

    Storage layout under *root* (default: ``$HERMES_HOME/evolve``,
    overridable via ``EVOLVE_DIR``):

      entities/{type}/{slug}.md        -- markdown entities (evolve-lite compatible)
      trajectories/{session_id}.jsonl  -- captured trajectories, one JSON line each

    Retrieval is case-insensitive term-overlap scoring of query tokens
    against each entity's ``trigger`` + ``content`` — no vector index, no
    server. ``save_trajectory`` writes the trajectory JSONL then invokes an
    injected ``guideline_generator(messages) -> list[dict]`` callable and
    saves each returned guideline as an entity. Generation failures never
    propagate — capture must not break a session.

    ``content_screen`` is an injected
    ``(content, trigger, rationale) -> list[str]`` callable that returns
    threat-pattern ids found in a guideline about to be written; a non-empty
    result refuses the write. It is injected rather than imported so this module
    stays stdlib-only — the provider wires in Hermes'
    ``tools.threat_patterns.scan_for_threats``.

    It takes the three fields rather than one pre-joined string because how they
    are *combined* is what decides whether a pattern matches: recall renders
    ``trigger`` and ``content`` onto one line, so a payload split across them
    only matches after joining. That knowledge belongs to the provider, which
    does the rendering, not here.
    """

    def __init__(
        self,
        root: Any,
        *,
        guideline_generator: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
        content_screen: Optional[Callable[[str, str, str], List[str]]] = None,
    ) -> None:
        self.root = Path(root)
        self.entities_dir = self.root / "entities"
        self.trajectories_dir = self.root / "trajectories"
        self._guideline_generator = guideline_generator
        self._content_screen = content_screen
        self._write_lock = threading.Lock()

    def _ensure_dir(self, path: Path) -> Path:
        """Create a store subdirectory, tightening the store root on the way in."""
        ensure_private_dir(self.root)
        return ensure_private_dir(path)

    # -- retrieval ------------------------------------------------------

    def _iter_entities(self) -> List[Dict[str, Any]]:
        if not self.entities_dir.is_dir():
            return []
        entities = []
        for md in sorted(self.entities_dir.glob("**/*.md")):
            try:
                entity = markdown_to_entity(md)
            except (OSError, ValueError):
                # ValueError covers UnicodeDecodeError, which is what a non-UTF-8
                # file under entities/ raises. Skip that one file: the caller
                # swallows exceptions from here, so letting it escape would zero
                # out recall for every guideline in the store, not just this one.
                continue
            if entity.get("content"):
                entity["_path"] = str(md)
                entity["_slug"] = md.stem
                entities.append(entity)
        return entities

    def get_guidelines(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        limit = max(0, int(limit or 0))
        entities = self._iter_entities()
        if limit == 0:
            return []

        query_tokens = set(_tokenize(query))
        if not query_tokens:
            # No usable query terms (blank, or nothing but stopwords). Returning
            # the first N entities in path order would inject whichever
            # guidelines happen to sort first — unrelated to anything the user
            # asked. Nothing scored, so return nothing.
            return []

        scored = []
        for entity in entities:
            haystack = f"{entity.get('trigger', '')} {entity.get('content', '')}"
            score = len(query_tokens & set(_tokenize(haystack)))
            if score > 0:
                scored.append((score, entity))
        # Stable sort by score desc; ties keep filesystem (path) order.
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entity for _, entity in scored[:limit]]

    # -- writes -----------------------------------------------------------

    def screen(self, content: str, trigger: str = "", rationale: str = "") -> List[str]:
        """Return threat-pattern ids found in a candidate guideline, or ``[]``.

        All three fields are screened: every one of them is injected verbatim on
        recall, so a clean ``content`` with a hostile ``trigger`` is still a way
        in. No screen injected means no findings.
        """
        if self._content_screen is None:
            return []
        try:
            return list(self._content_screen(content, trigger, rationale) or [])
        except Exception:
            # A broken screen must not become a way to block every write.
            logger.warning("evolve: content screen raised; allowing the write", exc_info=True)
            return []

    def save_guideline(self, content: str, trigger: str = "", rationale: str = "", type: str = "guideline") -> str:
        content = (content or "").strip()
        if not content:
            raise ValueError("content is required")
        trigger = (trigger or "").strip()
        rationale = (rationale or "").strip()
        findings = self.screen(content, trigger, rationale)
        if findings:
            raise ValueError(f"guideline rejected by content screen: {', '.join(sorted(findings))}")
        entity_type = type or "guideline"
        entity = {
            "type": entity_type,
            "trigger": trigger,
            "content": content,
            "rationale": rationale,
            "source": "hermes-evolve-lite",
        }
        with self._write_lock:
            self._ensure_dir(self.entities_dir)
            # The shared writer creates the per-type subdirectory itself, and it
            # is shared with four other harnesses — so create it here first,
            # with the mode we want, and let the writer find it already there.
            self._ensure_dir(self.entities_dir / (_sanitize_type(entity_type) or "guideline"))
            path = write_entity_file(self.entities_dir, entity)
            make_private(path)
        return str(path)

    def save_trajectory(
        self,
        messages: List[Dict[str, Any]],
        session_id: str,
        *,
        identity: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            from .trajectory_adapter import to_openai_trajectory
        except Exception:
            logger.warning("evolve: trajectory_adapter unavailable", exc_info=True)
            return {"trajectory_path": "", "guidelines": []}

        trajectory = to_openai_trajectory(messages)

        traj_path: Optional[Path] = None
        try:
            self._ensure_dir(self.trajectories_dir)
            safe_session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "session")
            traj_path = self.trajectories_dir / f"{safe_session}.jsonl"
            record: Dict[str, Any] = {"ts": time.time(), "session_id": session_id}
            # Who produced this trajectory. Recorded so an existing store stays
            # attributable after the fact, whatever EVOLVE_SCOPE was set to when
            # it was written. Absent keys are omitted rather than written null.
            for key, value in (identity or {}).items():
                if value:
                    record[key] = value
            record["messages"] = trajectory
            line = json.dumps(record, ensure_ascii=False)
            with self._write_lock:
                with open(traj_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                make_private(traj_path)
        except Exception:
            logger.warning("evolve: failed to write trajectory for session %s", session_id, exc_info=True)
            traj_path = None

        saved_guidelines: List[Dict[str, Any]] = []
        if self._guideline_generator is not None and trajectory:
            try:
                guidelines = self._guideline_generator(trajectory) or []
            except Exception:
                logger.warning("evolve: guideline generation failed", exc_info=True)
                guidelines = []

            for g in guidelines:
                if not isinstance(g, dict):
                    continue
                content = str(g.get("content") or "").strip()
                if not content:
                    continue
                trigger = str(g.get("trigger") or "")
                rationale = str(g.get("rationale") or "")
                # Screened before the write, not scrubbed after the read: a
                # guideline that trips the host's threat patterns would be
                # stripped at recall anyway, so storing it only guarantees a
                # store entry that can never be used.
                findings = self.screen(content, trigger, rationale)
                if findings:
                    logger.warning(
                        "evolve: generated guideline rejected by content screen (%s)",
                        ", ".join(sorted(findings)),
                    )
                    continue
                try:
                    path = self.save_guideline(
                        content=content,
                        trigger=trigger,
                        rationale=rationale,
                        type="guideline",
                    )
                    saved_guidelines.append({"path": path, "content": content})
                except Exception:
                    logger.warning("evolve: failed to save generated guideline", exc_info=True)

        return {
            "trajectory_path": str(traj_path) if traj_path else "",
            "guidelines": saved_guidelines,
        }
