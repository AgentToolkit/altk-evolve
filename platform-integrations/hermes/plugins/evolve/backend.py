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
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens, stopwords and single-char tokens dropped."""
    return [w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 1]


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

    def save_trajectory(self, messages: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
        """Persist a trajectory and (if configured) generate + save guidelines from it.

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

    def save_trajectory(self, messages: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
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
    """

    def __init__(
        self,
        root: Any,
        *,
        guideline_generator: Optional[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = None,
    ) -> None:
        self.root = Path(root)
        self.entities_dir = self.root / "entities"
        self.trajectories_dir = self.root / "trajectories"
        self._guideline_generator = guideline_generator
        self._write_lock = threading.Lock()

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
            # No usable query terms — return entities in stable (path) order.
            return entities[:limit]

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

    def save_guideline(self, content: str, trigger: str = "", rationale: str = "", type: str = "guideline") -> str:
        content = (content or "").strip()
        if not content:
            raise ValueError("content is required")
        entity = {
            "type": type or "guideline",
            "trigger": (trigger or "").strip(),
            "content": content,
            "rationale": (rationale or "").strip(),
            "source": "hermes-evolve-lite",
        }
        with self._write_lock:
            path = write_entity_file(self.entities_dir, entity)
        return str(path)

    def save_trajectory(self, messages: List[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
        try:
            from .trajectory_adapter import to_openai_trajectory
        except Exception:
            logger.warning("evolve: trajectory_adapter unavailable", exc_info=True)
            return {"trajectory_path": "", "guidelines": []}

        trajectory = to_openai_trajectory(messages)

        traj_path: Optional[Path] = None
        try:
            self.trajectories_dir.mkdir(parents=True, exist_ok=True)
            safe_session = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "session")
            traj_path = self.trajectories_dir / f"{safe_session}.jsonl"
            line = json.dumps(
                {"ts": time.time(), "session_id": session_id, "messages": trajectory},
                ensure_ascii=False,
            )
            with self._write_lock:
                with open(traj_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
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
                try:
                    path = self.save_guideline(
                        content=content,
                        trigger=str(g.get("trigger") or ""),
                        rationale=str(g.get("rationale") or ""),
                        type="guideline",
                    )
                    saved_guidelines.append({"path": path, "content": content})
                except Exception:
                    logger.warning("evolve: failed to save generated guideline", exc_info=True)

        return {
            "trajectory_path": str(traj_path) if traj_path else "",
            "guidelines": saved_guidelines,
        }
