"""Converters written by an LLM: code that turns one pipeline's traces into trajectories.

The traces of an observability platform change over time (a new agent, a new SDK version, a new field), so no single
hand-written converter fits them all. Instead every trace gets a JSON Schema (schema), and the schema decides which
converter handles it (engine): one is reused, extended or written (writer, prompts) and kept with its versions and
regression samples (store). Generated code is accepted only when its trajectory is valid and faithful to the trace
(checks), and it runs in a separate process with a time limit (sandbox)."""

from .engine import convert_trace
from .errors import ConverterError
from .store import converter_detail, converter_ids, delete_converter, list_converters

__all__ = ["ConverterError", "convert_trace", "converter_detail", "converter_ids", "delete_converter", "list_converters"]
