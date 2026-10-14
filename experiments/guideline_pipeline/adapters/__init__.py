"""Dataset adapters, by name."""

from experiments.guideline_pipeline.adapters.base import Adapter, AdapterRecord

ADAPTERS: dict[str, Adapter] = {}


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        available = ", ".join(sorted(ADAPTERS)) or "none registered"
        raise ValueError(f"Unknown adapter {name!r} (available: {available})") from None


__all__ = ["ADAPTERS", "Adapter", "AdapterRecord", "get_adapter"]
