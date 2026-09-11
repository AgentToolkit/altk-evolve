"""One registry for built-ins, installed entry points, and local factories."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Callable, cast

from altk_evolve.processing.models import ProcessingError, Processor


class ProcessorRegistry:
    def __init__(self):
        self._factories: dict[str, Callable[[], Processor]] = {}
        self._entries: dict = {}

    @classmethod
    def discover(cls, *, include_builtins: bool = True, installed: bool = True):
        registry = cls()
        if include_builtins:
            from altk_evolve.processing.builtin import GuidelineProcessor

            registry.register(GuidelineProcessor)
        if installed:
            for entry in entry_points(group="altk_evolve.processors"):
                if entry.name in registry._entries or entry.name in registry._factories:
                    raise ProcessingError(f"Duplicate processor: {entry.name}")
                registry._entries[entry.name] = entry
        return registry

    def register(self, factory: Callable[[], Processor]) -> None:
        descriptor = factory()
        if descriptor.id in self._factories or descriptor.id in self._entries:
            raise ProcessingError(f"Duplicate processor: {descriptor.id}")
        self._check(descriptor, descriptor.id)
        self._factories[descriptor.id] = factory

    @staticmethod
    def _check(processor: Processor, name: str):
        if processor.id != name or processor.api_version != 1:
            raise ProcessingError(f"Incompatible processor: {name}")
        processor.config_model.model_json_schema()

    def factory(self, name: str) -> Callable[[], Processor]:
        try:
            if name in self._factories:
                return self._factories[name]
            factory = cast(Callable[[], Processor], self._entries[name].load())
            self._check(factory(), name)
            self._factories[name] = factory
            return factory
        except Exception as exc:
            raise ProcessingError(f"Cannot load processor {name}: {exc}") from exc

    def inventory(self) -> list[dict]:
        items = []
        for name in sorted(self._factories.keys() | self._entries.keys()):
            try:
                processor = self.factory(name)()
                items.append(
                    {
                        "id": name,
                        "version": processor.version,
                        "api_version": processor.api_version,
                        "config_schema": processor.config_model.model_json_schema(),
                    }
                )
            except ProcessingError as exc:
                items.append({"id": name, "error": str(exc)})
        return items
