"""ALTK Forge: the ingestion server between an agent's observability traces and altk-evolve memory.

Layers, each depending only on the ones above it:

  core          where files live, errors, time, ids, JSON storage
  trajectory    the unified trajectory document: schemas, validation, spans, summary, slices
  sources       where traces come from (Langfuse, a folder, Phoenix) and how filters query them
  memory        where trajectories go (altk-evolve)
  llm           the LLM Forge itself calls, and its settings
  builtin       hand-written converters (Langfuse, OpenAI messages)
  converters    converters written by the LLM: schemas, sandbox, prompts, store, engine
  pipelines     integrations, filters, pipelines, sending, the worker
  web, cli      the HTTP server and API, and the command lines
"""
