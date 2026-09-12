"""One registry for built-ins, installed entry points, and local classes."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import cast

from altk_evolve.processing.models import ProcessingError, Processor


class ProcessorRegistry:
    def __init__(self):
        self._processors: dict[str, type[Processor]] = {}
        self._entries: dict = {}

    @classmethod
    def discover(cls, *, include_builtins: bool = True, installed: bool = True):
        registry = cls()
        if include_builtins:
            from altk_evolve.processing.builtin import GuidelineProcessor

            registry.register(GuidelineProcessor)
        if installed:
            for entry in entry_points(group="altk_evolve.processors"):
                if entry.name in registry._entries or entry.name in registry._processors:
                    raise ProcessingError(f"Duplicate processor: {entry.name}")
                registry._entries[entry.name] = entry
        return registry

    def register(self, processor_type: type[Processor]) -> None:
        if processor_type.id in self._processors or processor_type.id in self._entries:
            raise ProcessingError(f"Duplicate processor: {processor_type.id}")
        self._check(processor_type, processor_type.id)
        self._processors[processor_type.id] = processor_type

    @staticmethod
    def _check(processor: type[Processor], name: str):
        if not isinstance(processor, type) or processor.id != name or processor.api_version != 1:
            raise ProcessingError(f"Incompatible processor: {name}")
        if not callable(getattr(processor, "from_config", None)):
            raise ProcessingError(f"Processor {name} must implement from_config")
        processor.config_model.model_json_schema()

    def get(self, name: str) -> type[Processor]:
        try:
            if name in self._processors:
                return self._processors[name]
            processor_type = cast(type[Processor], self._entries[name].load())
            self._check(processor_type, name)
            self._processors[name] = processor_type
            return processor_type
        except Exception as exc:
            raise ProcessingError(f"Cannot load processor {name}: {exc}") from exc

    def inventory(self) -> list[dict]:
        items = []
        for name in sorted(self._processors.keys() | self._entries.keys()):
            try:
                processor = self.get(name)
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
