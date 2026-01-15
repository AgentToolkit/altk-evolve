"""
Phoenix Sync - Fetch trajectories from Arize Phoenix and generate tips.

This module provides functionality to:
1. Fetch agent trajectories from Phoenix's REST API
2. Deduplicate already-processed trajectories
3. Generate tips/guidelines from new trajectories
4. Store both trajectories and tips in the Kaizen backend
"""

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any

from kaizen.config.phoenix import phoenix_settings
from kaizen.config.kaizen import kaizen_config
from kaizen.frontend.client.kaizen_client import KaizenClient
from kaizen.llm.tips.tips import generate_tips
from kaizen.schema.core import Entity
from kaizen.schema.exceptions import NamespaceNotFoundException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kaizen.sync.phoenix")


@dataclass
class SyncResult:
    """Result of a sync operation."""
    processed: int
    skipped: int
    tips_generated: int
    errors: list[str]


class PhoenixSync:
    """Sync trajectories from Arize Phoenix to Kaizen."""

    def __init__(
        self,
        phoenix_url: str | None = None,
        namespace_id: str | None = None,
        project: str | None = None,
    ):
        """
        Initialize the PhoenixSync instance.
        
        Parameters:
            phoenix_url (str | None): Optional override for the Arize Phoenix API base URL; if omitted, the value from `phoenix_settings.url` is used.
            namespace_id (str | None): Optional override for the target Kaizen namespace ID; if omitted, the value from `kaizen_config.namespace_id` is used.
            project (str | None): Optional override for the Phoenix project name; if omitted, the value from `phoenix_settings.project` is used.
        """
        self.phoenix_url = phoenix_url or phoenix_settings.url
        self.project = project or phoenix_settings.project
        self.namespace_id = namespace_id or kaizen_config.namespace_id
        self.client = KaizenClient()

    def _ensure_namespace(self):
        """
        Ensure the Kaizen namespace identified by self.namespace_id exists, creating it if it does not.
        
        Creates the namespace through the Kaizen client when missing and logs the creation.
        """
        try:
            self.client.get_namespace_details(self.namespace_id)
        except NamespaceNotFoundException:
            self.client.create_namespace(self.namespace_id)
            logger.info(f"Created namespace: {self.namespace_id}")

    def _fetch_spans(self, limit: int = 1000) -> list[dict]:
        """
        Retrieve up to `limit` span objects from the Phoenix API, following pagination until the requested number is reached or no further pages are available.
        
        Parameters:
            limit (int): Maximum number of spans to fetch.
        
        Returns:
            list[dict]: A list of span objects returned by Phoenix.
        
        Raises:
            Exception: Propagates any exception raised while making HTTP requests or parsing responses.
        """
        spans = []
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
        """
        Return the set of span IDs for trajectories already stored in the target Kaizen namespace.
        
        Returns:
            set[str]: Span ID strings found in trajectory entities' metadata; returns an empty set if no span IDs are found or if the namespace does not exist.
        """
        try:
            entities = self.client.search_entities(
                namespace_id=self.namespace_id,
                filters={"type": "trajectory"},
                limit=10000
            )
            return {
                e.metadata.get("span_id")
                for e in entities
                if e.metadata and e.metadata.get("span_id")
            }
        except NamespaceNotFoundException:
            return set()

    def _parse_content(self, content: Any) -> Any:
        """
        Parse a value that may be a JSON or Python literal encoded as a string.
        
        If `content` is a string, this function first attempts to parse it as JSON. If JSON parsing fails, it then attempts to evaluate it as a Python literal (e.g., list, dict, tuple, number, boolean) using ast.literal_eval. If both attempts fail, the original string is returned unchanged. Non-string inputs are returned as-is.
        
        Parameters:
            content (Any): The value to parse, or a string containing a JSON object/array or a Python literal.
        
        Returns:
            Any: The parsed Python object when parsing succeeds, otherwise the original `content`.
        """
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
        """
        Collect prompt and completion messages from a span's `gen_ai` attributes.
        
        Searches the span's attributes for matching pairs `gen_ai.prompt.{i}.role`/`gen_ai.prompt.{i}.content`
        and `gen_ai.completion.{i}.role`/`gen_ai.completion.{i}.content`, parses each content value via
        _self._parse_content_, and returns the messages found.
        
        Returns:
            list[dict]: List of message dictionaries. Each dictionary contains:
                - 'index' (int): the numeric index extracted from the attribute keys.
                - 'type' (str): either 'prompt' or 'completion'.
                - 'role' (str): the role string from the attribute.
                - 'content' (Any): the parsed content value.
        """
        attrs = span.get("attributes", {})
        messages = []

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
                messages.append({
                    "index": i,
                    "type": "prompt",
                    "role": role,
                    "content": self._parse_content(content)
                })

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
                messages.append({
                    "index": i,
                    "type": "completion",
                    "role": role,
                    "content": self._parse_content(content)
                })

        return messages

    def _convert_to_openai_format(self, content: Any, role: str) -> dict:
        """
        Translate Anthropic-style message blocks into an OpenAI-compatible message dictionary.
        
        Accepts `content` that may be a string, any non-list value, or a list of block dictionaries.
        Recognizes block types:
        - "text": collected into the message content (skips "(no content)"),
        - "thinking": collected into a `thinking` field,
        - "tool_use": converted into `tool_calls` entries,
        - "tool_result": converted into `tool_results` entries.
        
        @param content: The message payload to convert; either a plain string, another scalar (coerced to string), or a list of block dicts as described above.
        @param role: The sender role (e.g., "assistant" or "user") which influences the output shape.
        
        @returns:
            dict: An OpenAI-like message. Possible keys:
            - "role": the provided role or "tool" for user tool results,
            - "content": concatenated text blocks or None when assistant has only tool calls,
            - "thinking": concatenated thinking blocks (if any),
            - "tool_calls": list of function call descriptors (if any),
            - "tool_results": list of tool result objects (only when returning a tool message).
        """
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
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })

            elif block_type == "tool_result":
                tool_results.append({
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": block.get("content", ""),
                    "is_error": block.get("is_error", False)
                })

        if role == "assistant":
            msg = {"role": "assistant"}
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

    def _extract_trajectory(self, span: dict) -> dict:
        """
        Builds a trajectory dictionary from a Phoenix span.
        
        Parses the span's attributes and extracted messages, converts each message into OpenAI-compatible message structures (including expanded tool result messages), and returns a consolidated trajectory record.
        
        Returns:
            dict: Trajectory with the following keys:
                - trace_id (str): Span's trace identifier.
                - span_id (str): Span's span identifier.
                - model (str): Model name from `gen_ai.request.model` or `"unknown"`.
                - timestamp: Span start time value.
                - messages (list[dict]): OpenAI-style messages. Tool result messages are represented as
                  {"role": "tool", "tool_call_id": <id>, "content": <text>} while regular messages follow
                  standard OpenAI message shapes (e.g., {"role": "user"|"assistant", "content": <text>, ...}).
                - usage (dict): Token usage with keys:
                    - prompt_tokens
                    - completion_tokens
                    - total_tokens
        """
        attrs = span.get("attributes", {})
        messages = self._extract_messages_from_span(span)

        openai_messages = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            converted = self._convert_to_openai_format(content, role)

            if converted.get("role") == "tool" and "tool_results" in converted:
                for result in converted["tool_results"]:
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": result["tool_call_id"],
                        "content": result["content"]
                    })
            else:
                openai_messages.append(converted)

        return {
            "trace_id": span["context"]["trace_id"],
            "span_id": span["context"]["span_id"],
            "model": attrs.get("gen_ai.request.model", "unknown"),
            "timestamp": span.get("start_time"),
            "messages": openai_messages,
            "usage": {
                "prompt_tokens": attrs.get("gen_ai.usage.prompt_tokens"),
                "completion_tokens": attrs.get("gen_ai.usage.completion_tokens"),
                "total_tokens": attrs.get("llm.usage.total_tokens")
            }
        }

    def _clean_trajectory(self, trajectory: dict) -> dict:
        """
        Remove messages that have no content and no tool calls, and strip `<system-reminder>` tags from string message content.
        
        Returns:
            dict: A trajectory dictionary identical to the input but with the `messages` list filtered so that:
                - Any message with neither `content` nor `tool_calls` is removed.
                - If a message's `content` is a string, any `<system-reminder>...</system-reminder>` sections are removed and the content is trimmed.
        """
        import re
        cleaned_messages = []

        for msg in trajectory.get("messages", []):
            if not msg.get("content") and not msg.get("tool_calls"):
                continue

            if msg.get("content"):
                content = msg["content"]
                if isinstance(content, str):
                    content = re.sub(
                        r'<system-reminder>.*?</system-reminder>',
                        '',
                        content,
                        flags=re.DOTALL
                    ).strip()
                    if not content:
                        continue
                    msg = {**msg, "content": content}

            cleaned_messages.append(msg)

        return {**trajectory, "messages": cleaned_messages}

    def _process_trajectory(self, trajectory: dict) -> int:
        """
        Store trajectory messages as 'trajectory' entities and generate/store tips as 'guideline' entities.
        
        Returns:
            int: The number of tips generated and stored.
        """
        # Store trajectory messages
        entities = []
        for msg in trajectory.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str) and content:
                entities.append(Entity(
                    type='trajectory',
                    content=content,
                    metadata={
                        "trace_id": trajectory["trace_id"],
                        "span_id": trajectory["span_id"],
                        "model": trajectory["model"],
                        "role": msg.get("role"),
                        "timestamp": trajectory["timestamp"],
                    }
                ))

        if entities:
            self.client.update_entities(
                namespace_id=self.namespace_id,
                entities=entities,
                enable_conflict_resolution=False
            )

        # Generate tips from the trajectory
        tips = generate_tips(trajectory["messages"])

        if tips:
            tip_entities = [
                Entity(
                    type='guideline',
                    content=tip,
                    metadata={
                        "source_trace_id": trajectory["trace_id"],
                        "source_span_id": trajectory["span_id"],
                    }
                )
                for tip in tips
            ]
            self.client.update_entities(
                namespace_id=self.namespace_id,
                entities=tip_entities,
                enable_conflict_resolution=True
            )

        return len(tips)

    def sync(
        self,
        limit: int = 100,
        include_errors: bool = False,
    ) -> SyncResult:
        """
        Orchestrates a full sync: fetches spans from Phoenix, filters and processes trajectories, and generates tips stored in Kaizen.
        
        Parameters:
            limit (int): Maximum number of spans to fetch from Phoenix for this run.
            include_errors (bool): If True, include spans with status "ERROR"; otherwise such spans are skipped.
        
        Returns:
            SyncResult: Counts of processed spans, skipped spans, tips generated, and a list of error messages encountered while processing.
        """
        logger.info(f"Starting sync from {self.phoenix_url} to namespace '{self.namespace_id}'")

        self._ensure_namespace()

        # Fetch spans from Phoenix
        spans = self._fetch_spans(limit)
        logger.info(f"Fetched {len(spans)} spans from Phoenix")

        # Get already processed span IDs
        processed_ids = self._get_processed_span_ids()
        logger.info(f"Found {len(processed_ids)} already processed spans")

        processed = 0
        skipped = 0
        tips_generated = 0
        errors = []

        for span in spans:
            # Filter to LLM request spans
            if span.get("name") != "litellm_request":
                continue

            # Filter errors if requested
            if not include_errors and span.get("status_code") == "ERROR":
                continue

            # Check if already processed
            span_id = span.get("context", {}).get("span_id")
            if span_id in processed_ids:
                skipped += 1
                continue

            # Only include spans with actual messages
            attrs = span.get("attributes", {})
            if not any(k.startswith("gen_ai.prompt.") for k in attrs):
                continue

            try:
                trajectory = self._extract_trajectory(span)
                trajectory = self._clean_trajectory(trajectory)

                if trajectory["messages"]:
                    tips_count = self._process_trajectory(trajectory)
                    processed += 1
                    tips_generated += tips_count
                    logger.info(
                        f"Processed span {span_id[:12]}... - "
                        f"generated {tips_count} tips"
                    )
            except Exception as e:
                error_msg = f"Error processing span {span_id}: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        result = SyncResult(
            processed=processed,
            skipped=skipped,
            tips_generated=tips_generated,
            errors=errors
        )

        logger.info(
            f"Sync complete: {processed} processed, {skipped} skipped, "
            f"{tips_generated} tips generated, {len(errors)} errors"
        )

        return result