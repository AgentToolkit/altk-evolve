"""Dataset adapters, by name."""

from collections.abc import Mapping

from experiments.guideline_pipeline.adapters.appworld import AppWorldAdapter
from experiments.guideline_pipeline.adapters.base import Adapter, AdapterRecord, ConfigurableAdapter
from experiments.guideline_pipeline.adapters.cuga import CugaAdapter

ADAPTERS: dict[str, Adapter] = {adapter.name: adapter for adapter in (AppWorldAdapter(), CugaAdapter())}


def get_adapter(name: str, options: Mapping[str, str] | None = None) -> Adapter:
    """The registered adapter, configured with options when any are given."""
    try:
        adapter = ADAPTERS[name]
    except KeyError:
        available = ", ".join(sorted(ADAPTERS)) or "none registered"
        raise ValueError(f"Unknown adapter {name!r} (available: {available})") from None
    if not options:
        return adapter
    if not isinstance(adapter, ConfigurableAdapter):
        raise ValueError(f"Adapter {name!r} takes no options")
    return adapter.configure(options)


__all__ = ["ADAPTERS", "Adapter", "AdapterRecord", "ConfigurableAdapter", "get_adapter"]
