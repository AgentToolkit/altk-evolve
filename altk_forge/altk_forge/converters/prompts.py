"""What the LLM is asked when it writes or changes a converter: the contract, the schema, a reference, the trace."""

import json
import re

from ..core.paths import PACKAGE
from .sandbox import ALLOWED_IMPORTS
from .schema import infer_schema, mixed_types

SCHEMA_TEXT = (PACKAGE / "trajectory" / "schema" / "trajectory.schema.json").read_text()


SYSTEM = (
    """You write Python converters for Forge. A converter turns one trace exported from an observability
platform into one trajectory document that follows the JSON schema below. Trajectories are what agent memory is built
from, so every LLM call, tool call, retrieval and memory access in the trace must be represented faithfully.

The contract:
- The file defines `def convert(trace: dict, context: dict) -> dict` at top level. It returns the trajectory.
- `trace` is the trace as the platform returns it, except that every string that held encoded JSON has already been
  replaced by the parsed value. `context` holds {"host": base URL of the platform or None, "source_kind": e.g.
  "langfuse" or "filesystem", "pipeline_id": str}.
- The trajectory's trace_id is str(trace["id"]), always: the pipeline stores and finds the trajectory by it.
- source_kind "filesystem": the trace is one JSON file someone saved, in an envelope {"id", "name" and "path" (the
  file's path in the folder), "uri" (file:// URI), "modified_at" (when the file was last written, not when the run
  happened), "size", "content" (the parsed file: whatever format the tool that wrote it uses)}. source.kind is "file",
  source.uri the envelope's uri. Everything else comes from content: times, ids, messages and scores only where
  content has them (a missing time stays out rather than being taken from modified_at).
- Only these imports are allowed: """
    + ", ".join(sorted(ALLOWED_IMPORTS))
    + """. No file, network, process or
  environment access; no open(), eval(), exec(), __import__ or dunder attributes. getattr()/hasattr() only with a
  constant attribute name not starting with _. The trace is plain JSON (dicts, lists, strings, numbers): use
  isinstance() and dict.get(), not attribute access.
- Deterministic: the same trace always gives exactly the same trajectory. No current time, no random ids; derive ids
  from the trace (observation ids are span ids).
- Robust: fields the samples have can be missing, null or of another shape in other traces of the same agent; use
  .get() and type checks rather than indexing that can fail. Never raise on optional data. A field listed under
  "Fields with more than one type" below holds each of those types somewhere in this very trace: check the type
  with isinstance() before calling .get() or indexing it, and handle every type listed.
- The result must validate against the schema (Draft 2020-12, additionalProperties false almost everywhere): drop
  null and empty optional fields rather than emitting them ("usage": {} or "tools": [] is wrong: leave the key out),
  keep required ones. Add no keys the schema does not define, wherever you would like to keep extra data.
- Every message in an llm span's input has its "source" (system_prompt, user, handoff, tool_result, ...: see
  InputMessage in the schema). It is required.
- The tools offered to a model are no messages, however the trace records them: a list next to the messages, span
  attributes (OpenTelemetry: llm.tools), or messages with role "tool" whose content is a tool definition
  ({"type": "function", "function": {...}}, as Langfuse logs them). They go to the llm span's "tools" (when a tool of
  that name ran) or "response_format" (when nothing executed it), never into "input". A role "tool" message whose
  content is a tool's result is a message, with source "tool_result".
- Never drop content to satisfy the schema. When a value has a type the schema does not allow where it goes (e.g. an
  LLM output whose content is a dict or list from structured output, where the schema wants a string), serialize it
  with json.dumps(value, ensure_ascii=False) instead of emptying it, picking one field or setting it to None. An LLM
  call's output is null only when the call produced nothing.
- One trace is one trajectory with exactly one turn span, its root: the turn's children are agent spans, and every
  llm, tool, retriever and memory span sits inside an agent. A conversation of several exchanges (a user and the
  agent taking turns) is still one turn: the first user message is the turn's input, the last answer its output,
  and the exchanges in between are the llm spans in order, each with the messages exactly as the trace has them.
  Never a turn span per step or per exchange. This is checked.
- agent_id names the agent and is the same in every trace of that agent: memory is shared per agent and step, so an
  agent_id taken from a file name, task id, run id or the trace id gives every trace its own memory. Take it from
  what the trace says about the agent (the framework's agent or node name, the name the system prompt gives it);
  when the trace names none, use one fixed id for the agent. This is checked.
- The code works for every trace of this shape, not just the one shown: no values of this trace (variable names,
  data keys, ids, tool names) hard-coded into the logic; read them from the trace.
- The turn's input is what the user asked. When the trace states the request in its own field (e.g. intent, task,
  question), that is the input, not the first message of a prompt, which may hold examples or instructions.
- A result the trace records (a score, reward, success, pass rate) goes into scores, with its value.
- Every list of chat messages in the trace (role system / user or human / assistant or ai, with content or value) is
  the input of an LLM call: one llm span with those messages as input (roles mapped to the schema's) and the
  model's reply as output. Code the model wrote and then ran is a tool span (name e.g. "execute_code") triggered by
  that llm span, with the code as input and the execution output as output. This is checked.
- Never flatten the trace to satisfy the schema, whatever its format: every agent that ran is an agent span, every
  call of a model an llm span, every tool or retrieval that ran a tool or retriever span, each with its own times,
  and the trace's own ids as span ids where it has them. A structured-output schema that nothing executed is the
  llm's response_format, not a tool.
  Only for a trace of Langfuse observations (a list "observations" with type CHAIN / AGENT / GENERATION / TOOL), and
  checked there: every LangGraph node that ran (a CHAIN or AGENT whose name equals its metadata.langgraph_node) is
  its own agent span with the node's name as agent_id, except __start__ / __end__ and nodes whose name cannot be an
  agent_id (it holds a dot or a space, e.g. PatchToolCallsMiddleware.before_agent, a framework hook around an
  agent): those are no agent, and what ran inside them goes to the agent around them. Every other CHAIN is plumbing,
  never an agent: RunnableSequence, RunnableParallel<...>, RunnableLambda, RunnableWithFallbacks, output parsers and
  the like. Leave it out and put what ran inside it in its agent: an observation belongs to the agent its
  metadata.langgraph_node names, however deep under plumbing it sits, and an llm span's step is that agent's id with
  the call's purpose (db_skills.tool_call, never RunnableParallel<raw>.tool_call). Every GENERATION is an llm span,
  every TOOL / RETRIEVER a tool / retriever span of the same name, each with span_id = the observation id and
  start_time / end_time from the observation.
- Answer with the complete file in one ```python block, nothing else."""
)


def _shorten(value, chars, items):
    if isinstance(value, dict):
        return {k: _shorten(v, chars, items) for k, v in value.items()}
    if isinstance(value, list):
        out = [_shorten(v, chars, items) for v in value[:items]]
        return out + [f"… {len(value) - items} more items"] if len(value) > items else out
    if isinstance(value, str) and len(value) > chars:
        return value[:chars] + f"… [{len(value)} chars]"
    return value


def sample_text(trace, budget=90_000):
    """A decoded trace for the prompt: long strings and arrays shortened until it fits the budget."""
    for chars, items in ((600, 60), (300, 40), (160, 25), (80, 15)):
        text = json.dumps(_shorten(trace, chars, items), ensure_ascii=False, indent=1)
        if len(text) <= budget:
            return text
    return text[:budget]


def mixed_text(trace, limit=60):
    """The trace's fields with more than one type, for the prompt; empty when every field has one."""
    mixed = mixed_types(infer_schema(trace))
    if not mixed:
        return ""
    lines = [f"  {path}: {' | '.join(kinds)}" for path, kinds in mixed[:limit]]
    more = f"\n  … {len(mixed) - limit} more" if len(mixed) > limit else ""
    return (
        "\n\nFields with more than one type in this trace (code that assumes one of them fails on the other; handle "
        "each):\n" + "\n".join(lines) + more
    )


def code_of(answer):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", answer, re.S)
    return max(blocks, key=len).strip() + "\n" if blocks else None


def prompt_new(trace, reference_code, reference_name):
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"""The trajectory schema:
```json
{SCHEMA_TEXT}
```

A working converter for a related kind of trace ({reference_name}). Reuse what applies (how it labels LLM input
messages, names steps, links tool calls, records errors and scores); adapt what does not fit this trace:
```python
{reference_code}
```

The trace to convert (decoded; long values shortened here, the real trace has them in full):
```json
{sample_text(trace)}
```{mixed_text(trace)}

Write the converter for traces shaped like this one.""",
        },
    ]


def prompt_extend(code, trace, added, failure=None):
    shown = "\n".join(added[:150]) + (f"\n… {len(added) - 150} more" if len(added) > 150 else "")
    if failure:
        what = f"""The converter fails on a new trace whose schema it knows: {failure}
Make it handle this trace."""
    else:
        what = f"""A new trace extends the schema the converter handles. It changes no type; it adds (path: type):
{shown}"""
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"""The trajectory schema:
```json
{SCHEMA_TEXT}
```

The current converter. The new version replaces it, so the trajectories it makes for the traces it already handles
must stay exactly the same: change only what the additions need, and map whatever they carry that the trajectory
schema has a place for:
```python
{code}
```

{what}

The new trace (decoded; long values shortened here):
```json
{sample_text(trace)}
```{mixed_text(trace)}

Answer with the complete updated file.""",
        },
    ]
