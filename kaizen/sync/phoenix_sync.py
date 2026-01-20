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
        self.phoenix_url = phoenix_url or phoenix_settings.url
        self.project = project or phoenix_settings.project
        self.namespace_id = namespace_id or kaizen_config.namespace_id
        self.client = KaizenClient()

    def _ensure_namespace(self):
        """Ensure the target namespace exists."""
        try:
            self.client.get_namespace_details(self.namespace_id)
        except NamespaceNotFoundException:
            self.client.create_namespace(self.namespace_id)
            logger.info(f"Created namespace: {self.namespace_id}")

    def _fetch_spans(self, limit: int = 1000) -> list[dict]:
        """Fetch spans from Phoenix, handling pagination."""
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
        """Get span_ids that have already been processed."""
        try:
            entities = self.client.search_entities(
                namespace_id=self.namespace_id,
                filters={"type": "trajectory"},
                limit=10000,
            )
            return {
                e.metadata.get("span_id")
                for e in entities
                if e.metadata and e.metadata.get("span_id")
            }
        except NamespaceNotFoundException:
            return set()

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
        """Extract a complete trajectory from a span."""
        attrs = span.get("attributes", {})
        messages = self._extract_messages_from_span(span)

        openai_messages = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            converted = self._convert_to_openai_format(content, role)

            if converted.get("role") == "tool" and "tool_results" in converted:
                for result in converted["tool_results"]:
                    openai_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": result["tool_call_id"],
                            "content": result["content"],
                        }
                    )
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
                "total_tokens": attrs.get("llm.usage.total_tokens"),
            },
        }

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
        """Process a single trajectory: store it and generate tips.

        Returns the number of tips generated.
        """
        # Store trajectory as a single entity with all messages
        messages = trajectory.get("messages", [])
        if messages:
            entity = Entity(
                type="trajectory",
                content=messages,
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

        # Generate tips from the trajectory
        tips = generate_tips(trajectory["messages"])

        if tips:
            tip_entities = [
                Entity(
                    type="guideline",
                    content=tip.content,
                    metadata={
                        "category": tip.category,
                        "rationale": tip.rationale,
                        "trigger": tip.trigger,
                        "source_trace_id": trajectory["trace_id"],
                        "source_span_id": trajectory["span_id"],
                    },
                )
                for tip in tips
            ]
            self.client.update_entities(
                namespace_id=self.namespace_id,
                entities=tip_entities,
                enable_conflict_resolution=True,
            )

        return len(tips)

    def sync(
        self,
        limit: int = 100,
        include_errors: bool = False,
    ) -> SyncResult:
        """
        Fetch new trajectories from Phoenix and generate tips.

        Args:
            limit: Maximum number of spans to fetch from Phoenix
            include_errors: Whether to include failed/error spans

        Returns:
            SyncResult with counts of processed, skipped, and tips generated
        """
        logger.info(
            f"Starting sync from {self.phoenix_url} to namespace '{self.namespace_id}'"
        )

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
            errors=errors,
        )

        logger.info(
            f"Sync complete: {processed} processed, {skipped} skipped, "
            f"{tips_generated} tips generated, {len(errors)} errors"
        )

        return result
