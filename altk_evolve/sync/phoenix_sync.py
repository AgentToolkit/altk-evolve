"""
Phoenix Sync - Fetch trajectories from Arize Phoenix and generate guidelines.

This module provides functionality to:
1. Fetch agent trajectories from Phoenix's REST API
2. Deduplicate already-processed trajectories
3. Generate guidelines from new trajectories
4. Store both trajectories and guidelines in the Evolve backend
"""

import json
import logging
import urllib.request
import warnings
from dataclasses import dataclass
from typing import Any, Optional

from altk_evolve.config.phoenix import phoenix_settings
from altk_evolve.config.evolve import evolve_config
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import generate_guidelines
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evolve.sync.phoenix")


@dataclass
class SyncResult:
    """Result of a sync operation."""

    processed: int
    skipped: int
    guidelines_generated: int
    errors: list[str]

    @property
    def tips_generated(self) -> int:
        """Temporary compatibility alias for one release cycle."""
        warnings.warn(
            "SyncResult.tips_generated is deprecated; use SyncResult.guidelines_generated instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.guidelines_generated


class PhoenixSync:
    """Sync trajectories from Arize Phoenix to Evolve."""

    def __init__(
        self,
        phoenix_url: str | None = None,
        namespace_id: str | None = None,
        project: str | None = None,
    ):
        self.phoenix_url = phoenix_url or phoenix_settings.url
        self.project = project or phoenix_settings.project
        self.namespace_id = namespace_id or evolve_config.namespace_id
        self.client = EvolveClient()

    def _ensure_namespace(self):
        """Ensure the target namespace exists."""
        try:
            self.client.get_namespace_details(self.namespace_id)
        except NamespaceNotFoundException:
            self.client.create_namespace(self.namespace_id)
            logger.info(f"Created namespace: {self.namespace_id}")

    def _fetch_spans(self, limit: int = 1000) -> list[dict]:
        """Fetch spans from Phoenix, handling pagination."""
        spans: list[dict] = []
        cursor = None

        while True:
            url = f"{self.phoenix_url}/v1/projects/{self.project}/spans?limit={min(limit - len(spans), 100)}"
            if cursor:
                url += f"&cursor={cursor}"

            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = json.loads(response.read().decode())
            except Exception as e:
                logger.error(f"Failed to fetch spans from Phoenix: {e}")
                raise

            spans.extend(data.get("data", []))
            cursor = data.get("next_cursor")

            if not cursor or len(spans) >= limit:
                break

        return spans

    def _get_processed_span_ids(self) -> set[str]:
        """Get span_ids that have already been processed."""
        try:
            entities = self.client.search_entities(
                namespace_id=self.namespace_id,
                filters={"type": "trajectory"},
                limit=10000,
            )
            return {str(e.metadata.get("span_id")) for e in entities if e.metadata and e.metadata.get("span_id")}
        except NamespaceNotFoundException:
            return set()

    def _get_processed_trace_ids(self) -> set[str]:
        """Get trace_ids that have already been processed."""
        try:
            entities = self.client.search_entities(
                namespace_id=self.namespace_id,
                filters={"type": "trajectory"},
                limit=10000,
            )
            return {str(e.metadata.get("trace_id")) for e in entities if e.metadata and e.metadata.get("trace_id")}
        except NamespaceNotFoundException:
            return set()

    def _is_llm_span(self, span: dict) -> bool:
        """Whether a span represents an actual LLM call, not a tool/agent/chain span.

        Phoenix's REST API promotes OpenInference's `openinference.span.kind` to a
        top-level `span_kind` field (`LLM`, `TOOL`, `CHAIN`, `AGENT`, ...) — the canonical,
        framework-agnostic signal. Fall back to attribute heuristics for spans from older or
        partial instrumentors that don't set it: OpenInference sets the generic
        `input.value`/`output.value` attributes on every span kind, so those alone can't
        distinguish an LLM call — only genuine LLM spans carry prompt/message attributes or
        a model name.
        """
        span_kind = span.get("span_kind")
        if span_kind:
            return bool(span_kind == "LLM")
        attrs = span.get("attributes") or {}
        if any(k.startswith("gen_ai.prompt.") for k in attrs):
            return True
        if any(k.startswith("llm.input_messages") or k.startswith("llm.output_messages") for k in attrs):
            return True
        if attrs.get("llm.model_name") or attrs.get("gen_ai.request.model"):
            return True
        return False

    def _group_spans_by_trace(self, spans: list[dict]) -> dict[str, list[dict]]:
        """Group spans by their trace_id."""
        by_trace: dict[str, list[dict]] = {}
        for span in spans:
            trace_id = span.get("context", {}).get("trace_id")
            if trace_id:
                by_trace.setdefault(trace_id, []).append(span)
        return by_trace

    def _span_id(self, span: dict) -> Optional[str]:
        ctx = span.get("context") or {}
        val = ctx.get("span_id")
        return str(val) if val is not None else None

    def _select_representative_span(self, llm_spans: list[dict]) -> dict:
        """Pick the most complete LLM span: the one with the latest start_time.

        Each LLM call in a sequential agent run receives the full accumulated message
        history as input (the agent framework's own conversation-state mechanism), so the
        last call chronologically already contains everything that happened before it —
        that span alone is the right unit to extract, no merge across spans needed.
        """
        return max(llm_spans, key=lambda s: s.get("start_time") or "")

    def _is_ancestor(self, parent_of: dict[str, Optional[str]], ancestor_id: Optional[str], span_id: Optional[str]) -> bool:
        """Whether `ancestor_id` is an ancestor of `span_id`, walking up `parent_id` links."""
        if ancestor_id is None or span_id is None:
            return False
        current = parent_of.get(span_id)
        seen: set[str] = set()
        while current and current not in seen:
            if current == ancestor_id:
                return True
            seen.add(current)
            current = parent_of.get(current)
        return False

    def _dedupe_nested_llm_spans(self, llm_spans: list[dict], parent_of: dict[str, Optional[str]]) -> list[dict]:
        """Collapse multi-layer instrumentation of one logical call to its innermost span.

        A single model call is often wrapped by several instrumentation layers that each
        emit their own LLM-kind span (e.g. smolagents' `LiteLLMModel.generate` ->
        litellm's `completion` -> the underlying `ChatCompletion` span). Keep only the
        innermost (deepest) span per such chain — it carries the most complete attributes.
        """
        ids = {self._span_id(s) for s in llm_spans}

        def wraps_another_llm_span(span: dict) -> bool:
            span_id = self._span_id(span)
            return any(other_id != span_id and self._is_ancestor(parent_of, span_id, other_id) for other_id in ids)

        return [s for s in llm_spans if not wraps_another_llm_span(s)]

    def _format_payload_summary(self, payload: Any) -> str:
        """Format a payload summary for secure logging (avoid PII)."""
        type_name = type(payload).__name__

        if isinstance(payload, str):
            length = len(payload)
            preview = payload[:50] + "..." if length > 50 else payload
            # Replace newlines in preview to keep logs on one line
            preview = preview.replace("\n", "\\n")
            return f"<{type_name} length={length} preview='{preview}'>"

        if isinstance(payload, (dict, list)):
            length = len(payload)
            return f"<{type_name} length={length}>"

        return f"<{type_name}>"

    def _parse_content(self, content: Any) -> Any:
        """Parse content which may be a string representation of a list/dict."""
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                try:
                    import ast

                    return ast.literal_eval(content)
                except (ValueError, SyntaxError):
                    return content
        return content

    def _extract_messages_from_span(self, span: dict) -> list[dict]:
        """Extract messages from a single span's attributes."""
        attrs = span.get("attributes") or {}
        messages = []

        # Determine which format is available per message side, preferring indexed over flat.
        # Real OpenInference LLM spans often emit BOTH input.value and llm.input_messages.*
        # keys simultaneously, so the source must be chosen independently per side to avoid
        # double-counting the same messages when both formats are present.
        has_indexed_input = any(k.startswith("llm.input_messages.") for k in attrs)
        has_indexed_output = any(k.startswith("llm.output_messages.") for k in attrs)

        # --- Input messages ---
        if has_indexed_input:
            input_indices: set[int] = set()
            for key in attrs:
                if key.startswith("llm.input_messages."):
                    parts = key.split(".")
                    if len(parts) >= 3 and parts[2].isdigit():
                        input_indices.add(int(parts[2]))

            for i in sorted(input_indices):
                role = attrs.get(f"llm.input_messages.{i}.message.role")
                content = attrs.get(f"llm.input_messages.{i}.message.content") or attrs.get(
                    f"llm.input_messages.{i}.message.contents.0.message_content.text"
                )
                tool_call_id = attrs.get(f"llm.input_messages.{i}.message.tool_call_id")

                # Collect indexed tool_calls: llm.input_messages.{i}.message.tool_calls.{j}.*
                tc_indices: set[int] = set()
                prefix = f"llm.input_messages.{i}.message.tool_calls."
                for key in attrs:
                    if key.startswith(prefix):
                        parts = key[len(prefix) :].split(".")
                        if parts and parts[0].isdigit():
                            tc_indices.add(int(parts[0]))
                tool_calls = []
                for j in sorted(tc_indices):
                    tc_prefix = f"llm.input_messages.{i}.message.tool_calls.{j}.tool_call."
                    tool_calls.append(
                        {
                            "tool_call.id": attrs.get(f"{tc_prefix}id", ""),
                            "tool_call.function.name": attrs.get(f"{tc_prefix}function.name", ""),
                            "tool_call.function.arguments": attrs.get(f"{tc_prefix}function.arguments", "{}"),
                        }
                    )

                if role:
                    indexed_msg: dict = {"index": i, "type": "prompt", "role": role, "content": content}
                    if tool_calls:
                        indexed_msg["tool_calls"] = tool_calls
                    if tool_call_id:
                        indexed_msg["tool_call_id"] = tool_call_id
                    messages.append(indexed_msg)
        else:
            # Non-indexed: flat llm.input_messages list or input.value JSON
            input_msgs = attrs.get("llm.input_messages")
            if input_msgs is None:
                input_val = attrs.get("input.value")
                if input_val:
                    try:
                        parsed_input = self._parse_content(input_val)
                        if isinstance(parsed_input, dict) and "messages" in parsed_input:
                            input_msgs = parsed_input["messages"]
                        elif isinstance(parsed_input, list):
                            input_msgs = parsed_input
                    except Exception as e:
                        logger.debug(f"Failed to parse input.value: {e}. Payload: {self._format_payload_summary(input_val)}")

            if input_msgs:
                if isinstance(input_msgs, str):
                    input_msgs = self._parse_content(input_msgs)
                if isinstance(input_msgs, list):
                    for i, msg in enumerate(input_msgs):
                        if isinstance(msg, str):
                            try:
                                msg = self._parse_content(msg)
                            except Exception as e:
                                logger.debug(f"Failed to parse input message string: {e}. Payload: {self._format_payload_summary(msg)}")
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get("message.role") or msg.get("role")
                        content = msg.get("message.content") or msg.get("content")
                        msg_tool_calls = msg.get("message.tool_calls") or msg.get("tool_calls")
                        tool_call_id = msg.get("message.tool_call_id") or msg.get("tool_call_id")
                        if role:
                            mapped_msg = {
                                "index": i,
                                "type": "prompt",
                                "role": role,
                                "content": self._parse_content(content),
                            }
                            if msg_tool_calls:
                                mapped_msg["tool_calls"] = msg_tool_calls
                            if tool_call_id:
                                mapped_msg["tool_call_id"] = tool_call_id
                            messages.append(mapped_msg)

        # --- Output messages ---
        if has_indexed_output:
            output_indices: set[int] = set()
            for key in attrs:
                if key.startswith("llm.output_messages."):
                    parts = key.split(".")
                    if len(parts) >= 3 and parts[2].isdigit():
                        output_indices.add(int(parts[2]))

            for i in sorted(output_indices):
                role = attrs.get(f"llm.output_messages.{i}.message.role")
                content = attrs.get(f"llm.output_messages.{i}.message.content") or attrs.get(
                    f"llm.output_messages.{i}.message.contents.0.message_content.text"
                )

                tc_indices_out: set[int] = set()
                prefix_out = f"llm.output_messages.{i}.message.tool_calls."
                for key in attrs:
                    if key.startswith(prefix_out):
                        parts = key[len(prefix_out) :].split(".")
                        if parts and parts[0].isdigit():
                            tc_indices_out.add(int(parts[0]))
                tool_calls_out = []
                for j in sorted(tc_indices_out):
                    tc_prefix_out = f"llm.output_messages.{i}.message.tool_calls.{j}.tool_call."
                    tool_calls_out.append(
                        {
                            "tool_call.id": attrs.get(f"{tc_prefix_out}id", ""),
                            "tool_call.function.name": attrs.get(f"{tc_prefix_out}function.name", ""),
                            "tool_call.function.arguments": attrs.get(f"{tc_prefix_out}function.arguments", "{}"),
                        }
                    )

                if role:
                    mapped_msg_out: dict = {"index": i, "type": "completion", "role": role, "content": content}
                    if tool_calls_out:
                        mapped_msg_out["tool_calls"] = tool_calls_out
                    messages.append(mapped_msg_out)
        else:
            # Non-indexed: flat llm.output_messages list or output.value
            output_msgs = attrs.get("llm.output_messages")
            if output_msgs is None:
                output_val = attrs.get("output.value")
                if output_val:
                    try:
                        parsed_output = self._parse_content(output_val)
                        if isinstance(parsed_output, list) and len(parsed_output) > 0 and "message" in parsed_output[0]:
                            output_msgs = [c["message"] for c in parsed_output]
                        elif isinstance(parsed_output, dict) and "choices" in parsed_output:
                            output_msgs = [c["message"] for c in parsed_output["choices"]]
                        else:
                            output_msgs = [{"role": "assistant", "content": output_val}]
                    except Exception as e:
                        logger.debug(f"Failed to parse output.value: {e}. Payload: {self._format_payload_summary(output_val)}")

            if output_msgs:
                if isinstance(output_msgs, str):
                    output_msgs = self._parse_content(output_msgs)
                if isinstance(output_msgs, list):
                    for i, msg in enumerate(output_msgs):
                        if isinstance(msg, str):
                            try:
                                msg = self._parse_content(msg)
                            except Exception as e:
                                logger.debug(f"Failed to parse output message string: {e}. Payload: {self._format_payload_summary(msg)}")
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get("message.role") or msg.get("role")
                        content = msg.get("message.content") or msg.get("content")
                        msg_tool_calls = msg.get("message.tool_calls") or msg.get("tool_calls")
                        tool_call_id = msg.get("message.tool_call_id") or msg.get("tool_call_id")
                        if role:
                            mapped_msg = {
                                "index": i,
                                "type": "completion",
                                "role": role,
                                "content": self._parse_content(content),
                            }
                            if msg_tool_calls:
                                mapped_msg["tool_calls"] = msg_tool_calls
                            if tool_call_id:
                                mapped_msg["tool_call_id"] = tool_call_id
                            messages.append(mapped_msg)

        if messages:
            return messages

        # Fallback to GenAI semantic conventions (original code)
        # Extract prompt messages
        prompt_indices = set()
        for key in attrs:
            if key.startswith("gen_ai.prompt.") and key.endswith(".role"):
                idx = int(key.split(".")[2])
                prompt_indices.add(idx)

        for i in sorted(prompt_indices):
            role = attrs.get(f"gen_ai.prompt.{i}.role")
            content = attrs.get(f"gen_ai.prompt.{i}.content")
            if role and content is not None:
                messages.append(
                    {
                        "index": i,
                        "type": "prompt",
                        "role": role,
                        "content": self._parse_content(content),
                    }
                )

        # Extract completion messages
        completion_indices = set()
        for key in attrs:
            if key.startswith("gen_ai.completion.") and key.endswith(".role"):
                idx = int(key.split(".")[2])
                completion_indices.add(idx)

        for i in sorted(completion_indices):
            role = attrs.get(f"gen_ai.completion.{i}.role")
            content = attrs.get(f"gen_ai.completion.{i}.content")
            if role and content is not None:
                messages.append(
                    {
                        "index": i,
                        "type": "completion",
                        "role": role,
                        "content": self._parse_content(content),
                    }
                )

        return messages

    def _convert_to_openai_format(self, content: Any, role: str) -> dict:
        """Convert Anthropic message format to OpenAI format."""
        if isinstance(content, str):
            return {"role": role, "content": content}

        if not isinstance(content, list):
            return {"role": role, "content": str(content)}

        text_parts = []
        tool_calls = []
        tool_results = []
        thinking_parts = []

        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue

            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "")
                if text and text != "(no content)":
                    text_parts.append(text)

            elif block_type == "thinking":
                thinking = block.get("thinking", "")
                if thinking:
                    thinking_parts.append(thinking)

            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    }
                )

            elif block_type == "tool_result":
                tool_results.append(
                    {
                        "tool_call_id": block.get("tool_use_id", ""),
                        "content": block.get("content", ""),
                        "is_error": block.get("is_error", False),
                    }
                )

        if role == "assistant":
            msg: dict[str, str | list | None] = {"role": "assistant"}
            if thinking_parts:
                msg["thinking"] = "\n\n".join(thinking_parts)
            if text_parts:
                msg["content"] = "\n\n".join(text_parts)
            elif not tool_calls:
                msg["content"] = None
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return msg

        elif role == "user" and tool_results:
            return {"role": "tool", "tool_results": tool_results}

        else:
            content_text = "\n\n".join(text_parts) if text_parts else ""
            return {"role": role, "content": content_text}

    def _extract_tools_from_span(self, span: dict) -> list[dict] | None:
        """Extract tool definitions from a span's attributes in OpenAI tools format.

        Tries three attribute conventions in order:
        1. llm.invocation_parameters (LiteLLM GenAI convention) — JSON dict with a "tools" key
        2. llm.tools as a JSON array (OpenInference flat convention)
        3. Indexed llm.tools.{i}.tool.* keys (OpenInference indexed convention)
        """
        attrs = span.get("attributes") or {}

        invocation_params = attrs.get("llm.invocation_parameters")
        if invocation_params:
            try:
                params = json.loads(invocation_params) if isinstance(invocation_params, str) else invocation_params
                if isinstance(params, dict):
                    tools = params.get("tools")
                    if isinstance(tools, list) and tools:
                        return tools
            except (json.JSONDecodeError, Exception):
                pass

        tools_attr = attrs.get("llm.tools")
        if tools_attr is not None:
            try:
                tools = json.loads(tools_attr) if isinstance(tools_attr, str) else tools_attr
                if isinstance(tools, list) and tools:
                    # OpenInference stores each tool as {"tool.json_schema": "<json string>"}
                    # where the schema value is already in OpenAI function-tool format.
                    openai_tools = []
                    for item in tools:
                        if isinstance(item, dict):
                            schema_str = item.get("tool.json_schema")
                            if schema_str is not None:
                                try:
                                    schema = json.loads(schema_str) if isinstance(schema_str, str) else schema_str
                                    openai_tools.append(schema)
                                    continue
                                except (json.JSONDecodeError, Exception):
                                    pass
                        openai_tools.append(item)
                    if openai_tools:
                        return openai_tools
            except (json.JSONDecodeError, Exception):
                pass

        # Indexed OpenInference format from REST API: llm.tools.{i}.tool.json_schema
        # (Phoenix REST API expands llm.tools list into flat indexed attributes)
        tool_indices: set[int] = set()
        for key in attrs:
            if key.startswith("llm.tools."):
                parts = key.split(".")
                if len(parts) >= 3 and parts[2].isdigit():
                    tool_indices.add(int(parts[2]))

        if tool_indices:
            tools = []
            for i in sorted(tool_indices):
                json_schema_str = attrs.get(f"llm.tools.{i}.tool.json_schema")
                if json_schema_str:
                    try:
                        schema = json.loads(json_schema_str) if isinstance(json_schema_str, str) else json_schema_str
                        tools.append(schema)
                        continue
                    except (json.JSONDecodeError, Exception):
                        pass
                # Fall back to building the tool from name/description/parameters parts
                name = attrs.get(f"llm.tools.{i}.tool.name")
                if not name:
                    continue
                tool: dict = {"type": "function", "function": {"name": name}}
                description = attrs.get(f"llm.tools.{i}.tool.description")
                if description:
                    tool["function"]["description"] = description
                json_schema = attrs.get(f"llm.tools.{i}.tool.json_schema")
                if json_schema:
                    try:
                        schema = json.loads(json_schema) if isinstance(json_schema, str) else json_schema
                        tool["function"]["parameters"] = schema
                    except (json.JSONDecodeError, Exception):
                        pass
                tools.append(tool)
            if tools:
                return tools

        return None

    def _convert_openinference_tool_calls(self, tool_calls: list) -> list:
        """Convert OpenInference tool_calls to OpenAI format.

        OpenInference: {"tool_call.function.name": ..., "tool_call.id": ..., "tool_call.function.arguments": ...}
        OpenAI:        {"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}
        """
        result = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            name = tc.get("tool_call.function.name")
            if name is not None:
                arguments = tc.get("tool_call.function.arguments", "{}")
                result.append(
                    {
                        "id": tc.get("tool_call.id", ""),
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                        },
                    }
                )
            elif "id" in tc or "function" in tc:
                result.append(tc)
        return result

    def _assemble_openai_messages(self, messages: list[dict]) -> list[dict]:
        """Convert extracted prompt/completion message dicts into OpenAI chat format."""
        openai_messages = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            raw_tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            converted = self._convert_to_openai_format(content, role)

            if converted.get("role") == "tool" and "tool_results" in converted:
                # Anthropic content-block format — tool_call_id already embedded
                for result in converted["tool_results"]:
                    openai_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        }
                    )
            elif role == "tool" and tool_call_id:
                # OpenInference format — tool_call_id was on the span attribute
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": converted.get("content", ""),
                    }
                )
            elif role == "assistant" and raw_tool_calls:
                # OpenInference format — convert tool_calls from OpenInference to OpenAI
                openai_tool_calls = self._convert_openinference_tool_calls(raw_tool_calls)
                if openai_tool_calls:
                    converted["tool_calls"] = openai_tool_calls
                    # Assistant tool-call messages have no text content
                    if converted.get("content") in (None, "None", ""):
                        converted.pop("content", None)
                openai_messages.append(converted)
            else:
                openai_messages.append(converted)

        return openai_messages

    def _extract_usage(self, attrs: dict) -> dict:
        return {
            "prompt_tokens": next(
                (
                    v
                    for v in [
                        attrs.get("gen_ai.usage.prompt_tokens"),
                        attrs.get("llm.token_count.prompt"),
                        attrs.get("llm.usage.prompt_tokens"),
                    ]
                    if v is not None
                ),
                None,
            ),
            "completion_tokens": next(
                (
                    v
                    for v in [
                        attrs.get("gen_ai.usage.completion_tokens"),
                        attrs.get("llm.token_count.completion"),
                        attrs.get("llm.usage.completion_tokens"),
                    ]
                    if v is not None
                ),
                None,
            ),
            "total_tokens": next(
                (
                    v
                    for v in [
                        attrs.get("gen_ai.usage.total_tokens"),
                        attrs.get("llm.token_count.total"),
                        attrs.get("llm.usage.total_tokens"),
                    ]
                    if v is not None
                ),
                None,
            ),
        }

    def _extract_trajectory(self, span: dict) -> dict:
        """Extract a complete trajectory from a single span (its own input + output)."""
        attrs = span.get("attributes") or {}
        messages = self._extract_messages_from_span(span)

        return {
            "trace_id": span["context"]["trace_id"],
            "span_id": span["context"]["span_id"],
            "model": attrs.get("gen_ai.request.model") or attrs.get("llm.model_name", "unknown"),
            "timestamp": span.get("start_time"),
            "messages": self._assemble_openai_messages(messages),
            "tools": self._extract_tools_from_span(span),
            "usage": self._extract_usage(attrs),
        }

    def _build_trajectory_for_trace(self, trace_id: str, spans: list[dict]) -> Optional[dict]:
        """Build the trajectory for one trace from its candidate LLM spans.

        Each LLM call in a sequential agent run receives the full accumulated message
        history as input (the agent framework's own conversation-state mechanism), so the
        single most-complete LLM span — after collapsing multi-layer instrumentation of one
        logical call to its innermost span — already contains everything that happened in
        the trace. No cross-span merge is needed or correct: an assistant-role message in
        the trajectory must mean "the model produced this," and only a genuine LLM span's
        own output satisfies that.
        """
        parent_of: dict[str, Optional[str]] = {sid: s.get("parent_id") for s in spans if (sid := self._span_id(s)) is not None}
        llm_spans = self._dedupe_nested_llm_spans([s for s in spans if self._is_llm_span(s)], parent_of)

        if not llm_spans:
            return None

        representative = self._select_representative_span(llm_spans)
        return self._extract_trajectory(representative)

    def _clean_trajectory(self, trajectory: dict) -> dict:
        """Clean up a trajectory by removing system reminders."""
        import re

        cleaned_messages = []

        for msg in trajectory.get("messages", []):
            if not msg.get("content") and not msg.get("tool_calls"):
                continue

            if msg.get("content"):
                content = msg["content"]
                if isinstance(content, str):
                    content = re.sub(
                        r"<system-reminder>.*?</system-reminder>",
                        "",
                        content,
                        flags=re.DOTALL,
                    ).strip()
                    if not content:
                        continue
                    msg = {**msg, "content": content}

            cleaned_messages.append(msg)

        return {**trajectory, "messages": cleaned_messages}

    def _process_trajectory(self, trajectory: dict) -> int:
        """Process a single trajectory: store it and generate guidelines.

        Returns the number of guidelines generated.
        """
        # Store trajectory as a single entity with all messages
        messages = trajectory.get("messages", [])
        if messages:
            entity = Entity(
                type="trajectory",
                content=json.dumps(messages),
                metadata={
                    "trace_id": trajectory["trace_id"],
                    "span_id": trajectory["span_id"],
                    "model": trajectory["model"],
                    "timestamp": trajectory["timestamp"],
                    "message_count": len(messages),
                    "usage": trajectory.get("usage"),
                },
            )

            self.client.update_entities(
                namespace_id=self.namespace_id,
                entities=[entity],
                enable_conflict_resolution=False,
            )

        # Generate guidelines from the trajectory (returns one result per subtask)
        results = generate_guidelines(trajectory["messages"])

        guideline_entities = [
            Entity(
                type="guideline",
                content=guideline.content,
                metadata={
                    "category": guideline.category,
                    "rationale": guideline.rationale,
                    "trigger": guideline.trigger,
                    "implementation_steps": guideline.implementation_steps,
                    "source_task_id": trajectory["trace_id"],
                    "source_span_id": trajectory["span_id"],
                    "task_description": result.task_description,
                    "creation_mode": "auto-phoenix",
                },
            )
            for result in results
            for guideline in result.guidelines
        ]
        if guideline_entities:
            self.client.update_entities(
                namespace_id=self.namespace_id,
                entities=guideline_entities,
                enable_conflict_resolution=True,
            )

        return sum(len(r.guidelines) for r in results)

    def sync(
        self,
        limit: int = 100,
        include_errors: bool = False,
    ) -> SyncResult:
        """
        Fetch new trajectories from Phoenix and generate guidelines.

        Args:
            limit: Maximum number of spans to fetch from Phoenix
            include_errors: Whether to include failed/error spans

        Returns:
            SyncResult with counts of processed, skipped, and guidelines generated
        """
        logger.info(f"Starting sync from {self.phoenix_url} to namespace '{self.namespace_id}'")

        self._ensure_namespace()

        # Fetch spans from Phoenix
        spans = self._fetch_spans(limit)
        logger.info(f"Fetched {len(spans)} spans from Phoenix")

        # Get already processed trace IDs (one trajectory entity stored per trace)
        processed_trace_ids = self._get_processed_trace_ids()
        logger.info(f"Found {len(processed_trace_ids)} already processed traces")

        processed = 0
        skipped = 0
        guidelines_generated = 0
        errors = []

        # First pass: filter to LLM spans from unprocessed traces
        skipped_trace_ids: set[str] = set()
        candidate_spans: list[dict] = []
        for span in spans:
            if not include_errors and span.get("status_code") == "ERROR":
                continue

            trace_id = span.get("context", {}).get("trace_id")
            if trace_id in processed_trace_ids:
                skipped_trace_ids.add(trace_id)
                continue

            if not self._is_llm_span(span):
                continue

            candidate_spans.append(span)

        skipped = len(skipped_trace_ids)

        # Second pass: build one trajectory per trace from all of its candidate spans
        spans_by_trace = self._group_spans_by_trace(candidate_spans)
        logger.info(f"Selected {len(spans_by_trace)} traces from {len(candidate_spans)} candidate spans")

        for trace_id, trace_spans in spans_by_trace.items():
            try:
                trajectory = self._build_trajectory_for_trace(trace_id, trace_spans)
                if trajectory is None:
                    continue
                trajectory = self._clean_trajectory(trajectory)

                if trajectory["messages"]:
                    guidelines_count = self._process_trajectory(trajectory)
                    processed += 1
                    guidelines_generated += guidelines_count
                    logger.info(f"Processed trace {trajectory['trace_id'][:12]}... - generated {guidelines_count} guidelines")
            except Exception as e:
                error_msg = f"Error processing trace {trace_id}: {e}"
                logger.exception(error_msg)
                errors.append(error_msg)

        result = SyncResult(
            processed=processed,
            skipped=skipped,
            guidelines_generated=guidelines_generated,
            errors=errors,
        )

        logger.info(
            f"Sync complete: {processed} processed, {skipped} skipped, {guidelines_generated} guidelines generated, {len(errors)} errors"
        )

        return result
