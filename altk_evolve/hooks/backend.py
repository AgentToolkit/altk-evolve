"""The read/metadata-patch capability supplied to memory hooks."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from altk_evolve.backend.writes import MetadataPatch
from altk_evolve.schema.exceptions import EvolveException

if TYPE_CHECKING:
    from altk_evolve.backend.base import BaseEntityBackend
    from altk_evolve.schema.core import Namespace, RecordedEntity


@dataclass
class _PendingPatches:
    backend: BaseEntityBackend
    namespace_id: str
    patches: list[MetadataPatch] = field(default_factory=list)
    closed: bool = False


_pending: ContextVar[_PendingPatches | None] = ContextVar("hook_metadata_patches", default=None)

# Set while a HookBackend method dispatches nested hooks: a view created by
# those hooks records it as its parent and is revoked along with it. It is set
# inside the method, so the link follows the capability onto whatever thread
# uses it, and work outside a ``context.backend`` call is not linked.
_calling_view: ContextVar[HookBackend | None] = ContextVar("hook_calling_view", default=None)

# Makes revoking a view and accepting a patch through it mutually exclusive.
_revoke_lock = threading.Lock()


def _reset_revoke_lock_after_fork() -> None:
    global _revoke_lock
    _revoke_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_revoke_lock_after_fork)


@contextmanager
def collect_metadata_patches(backend: BaseEntityBackend, namespace_id: str):
    pending = _PendingPatches(backend, namespace_id)
    token = _pending.set(pending)
    try:
        yield pending.patches
    finally:
        pending.closed = True
        _pending.reset(token)


def proposed_metadata(backend: BaseEntityBackend, entity_id: str, metadata: dict) -> dict:
    """Overlay this preparation's patches for policy decisions, leaving stored versions intact."""
    result = deepcopy(metadata)
    pending = _pending.get()
    if pending is not None and pending.backend is backend:
        for patch in pending.patches:
            if patch.entity_id == entity_id:
                result.update(patch.patch)
    return result


class HookBackend:
    """Hooks may read memory and propose metadata patches, not control storage.

    During preparation patches join the output commit. Outside preparation, a
    patch uses the backend's ordinary write path. Callbacks must finish before
    returning; retaining this capability for detached work is unsupported. A
    native plugin's view is revoked when its call ends or times out, and so is
    every view created by hooks that call dispatched.
    """

    def __init__(self, backend: BaseEntityBackend):
        self.__backend = backend
        pending = _pending.get()
        self.__pending = pending if pending is not None and pending.backend is backend else None
        self.__revoked = False
        self.__parent = _calling_view.get()

    def _for_call(self) -> HookBackend:
        """Return a view of this capability for one plugin call, revocable on its own."""
        view = copy(self)
        view.__revoked = False
        return view

    def _revoke(self) -> None:
        with _revoke_lock:
            self.__revoked = True

    def _check_revoked(self) -> None:
        view: HookBackend | None = self
        while view is not None:
            if view.__revoked:
                raise EvolveException("The hook call that received this backend has finished")
            view = view.__parent

    @contextmanager
    def _dispatching(self):
        token = _calling_view.set(self)
        try:
            yield
        finally:
            _calling_view.reset(token)

    def _check(self, namespace_id: str):
        self._check_revoked()
        if self.__pending is not None:
            if self.__pending.closed:
                raise EvolveException("Hook preparation has finished")
            if namespace_id != self.__pending.namespace_id:
                raise EvolveException("A hook cannot patch or read another namespace during preparation")

    def _read(self, namespace_id, query=None, filters=None, limit=10):
        self._check(namespace_id)
        entities = self.__backend._search_entities_impl(namespace_id, query, filters, limit)
        if self.__pending is not None:
            entities = deepcopy(entities)
            for entity in entities:
                for patch in self.__pending.patches:
                    if patch.entity_id == entity.id:
                        entity.metadata = {**entity.metadata, **patch.patch}
        return entities

    def search_entities(
        self, namespace_id: str, query: str | None = None, filters: dict | None = None, limit: int = 10
    ) -> list[RecordedEntity]:
        from altk_evolve.hooks.manager import dispatch_memory_post_read

        entities = self._read(namespace_id, query, filters, limit)
        self._check(namespace_id)
        with self._dispatching():
            return dispatch_memory_post_read(self.__backend, namespace_id, entities, query=query, filters=filters)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        self._check(namespace_id)
        return self.__backend.get_namespace_details(namespace_id)

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        self._check(namespace_id)
        with self._dispatching():
            return self._update_entity_metadata(namespace_id, entity_id, metadata_patch)

    def _update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        from altk_evolve.hooks.manager import dispatch_memory_pre_metadata_patch, dispatch_memory_post_read

        if self.__pending is None:
            # The backend's update_entity_metadata template, with a revocation
            # check after the patch hooks, right before the write starts.
            patch = dispatch_memory_pre_metadata_patch(self.__backend, namespace_id, entity_id, metadata_patch)
            self._check(namespace_id)
            entity = self.__backend._update_entity_metadata_impl(namespace_id, entity_id, patch)
            transformed = dispatch_memory_post_read(self.__backend, namespace_id, [entity])
            return transformed[0] if transformed else entity
        patch = dispatch_memory_pre_metadata_patch(self.__backend, namespace_id, entity_id, metadata_patch)
        found = self._read(namespace_id, filters={"id": entity_id}, limit=1)
        if not found:
            raise EvolveException(f"Entity {entity_id!r} not found")
        with _revoke_lock:
            self._check(namespace_id)
            self.__pending.patches.append(MetadataPatch(namespace_id, entity_id, deepcopy(patch)))
        entity = found[0].model_copy(deep=True)
        entity.metadata = {**entity.metadata, **patch}
        transformed = dispatch_memory_post_read(self.__backend, namespace_id, [entity])
        return transformed[0] if transformed else entity
