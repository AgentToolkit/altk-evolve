#!/usr/bin/env python3
"""
Extract agent trajectories from Arize Phoenix and convert to OpenAI chat completion format.

This script fetches spans from Phoenix traces and transforms them into a format
compatible with OpenAI's chat completion messages, including:
- User utterances
- Agent reasoning (thinking)
- Tool calls
- Tool responses
- Agent responses
"""

import json
import argparse
from typing import Any
import urllib.request


def fetch_spans(base_url: str, limit: int = 1000) -> list[dict]:
    """Fetch all spans from Phoenix, handling pagination."""
    spans: list[dict[str, Any]] = []
    cursor = None

    while True:
        url = f"{base_url}/v1/projects/default/spans?limit={min(limit - len(spans), 100)}"
        if cursor:
            url += f"&cursor={cursor}"

        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())

        spans.extend(data.get("data", []))
        cursor = data.get("next_cursor")

        if not cursor or len(spans) >= limit:
            break

    return spans


def parse_content(content: Any) -> Any:
    """Parse content which may be a string representation of a list/dict."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Try to parse as Python literal
            try:
                import ast

                return ast.literal_eval(content)
            except (ValueError, SyntaxError):
                return content
    return content


def extract_messages_from_span(span: dict) -> list[dict]:
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
            messages.append({"index": i, "type": "prompt", "role": role, "content": parse_content(content)})

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
            messages.append({"index": i, "type": "completion", "role": role, "content": parse_content(content)})

    return messages


def convert_anthropic_to_openai(content: Any, role: str) -> dict:
    """Convert Anthropic message format to OpenAI format."""

    if isinstance(content, str):
        return {"role": role, "content": content}

    if not isinstance(content, list):
        return {"role": role, "content": str(content)}

    # Process list of content blocks
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
                    "function": {"name": block.get("name", ""), "arguments": json.dumps(block.get("input", {}))},
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

    # Build OpenAI format message
    if role == "assistant":
        msg = {"role": "assistant"}

        # Include thinking as a separate field (non-standard but useful)
        if thinking_parts:
            msg["thinking"] = "\n\n".join(thinking_parts)

        if text_parts:
            msg["content"] = "\n\n".join(text_parts)
        elif not tool_calls:
            msg["content"] = None  # type: ignore[assignment]

        if tool_calls:
            msg["tool_calls"] = tool_calls  # type: ignore[assignment]

        return msg

    elif role == "user" and tool_results:
        # Tool results come back as "user" role in Anthropic format
        # In OpenAI format, each tool result is a separate message
        return {"role": "tool", "tool_results": tool_results}

    else:
        # Regular user message
        content_text = "\n\n".join(text_parts) if text_parts else ""
        return {"role": role, "content": content_text}


def extract_trajectory(span: dict) -> dict:
    """Extract a complete trajectory from a span."""
    attrs = span.get("attributes", {})
    messages = extract_messages_from_span(span)

    openai_messages = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        converted = convert_anthropic_to_openai(content, role)

        # Handle tool results (expand into individual messages)
        if converted.get("role") == "tool" and "tool_results" in converted:
            for result in converted["tool_results"]:
                openai_messages.append({"role": "tool", "tool_call_id": result["tool_call_id"], "content": result["content"]})
        else:
            openai_messages.append(converted)

    # Add the completion if not already included
    if messages and messages[-1]["type"] != "completion":
        completion_indices = set()
        for key in attrs:
            if key.startswith("gen_ai.completion.") and key.endswith(".role"):
                idx = int(key.split(".")[2])
                completion_indices.add(idx)

        for i in sorted(completion_indices):
            role = attrs.get(f"gen_ai.completion.{i}.role")
            content = attrs.get(f"gen_ai.completion.{i}.content")
            if role and content:
                converted = convert_anthropic_to_openai(parse_content(content), role)
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


def filter_system_reminders(text: str) -> str:
    """Remove system reminders from text content."""
    import re

    return re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.DOTALL).strip()


def clean_trajectory(trajectory: dict, remove_system_reminders: bool = True) -> dict:
    """Clean up a trajectory by removing noise and system messages."""
    cleaned_messages = []

    for msg in trajectory.get("messages", []):
        # Skip empty messages
        if not msg.get("content") and not msg.get("tool_calls"):
            continue

        # Clean content
        if remove_system_reminders and msg.get("content"):
            content = msg["content"]
            if isinstance(content, str):
                content = filter_system_reminders(content)
                if not content:
                    continue
                msg = {**msg, "content": content}

        cleaned_messages.append(msg)

    return {**trajectory, "messages": cleaned_messages}


def get_trajectories(
    base_url: str = "http://localhost:6006", limit: int = 100, include_errors: bool = False, clean: bool = True
) -> list[dict]:
    """
    Fetch and extract agent trajectories from Phoenix.

    Args:
        base_url: Phoenix server URL
        limit: Maximum number of spans to fetch
        include_errors: Whether to include failed spans
        clean: Whether to clean system reminders from messages

    Returns:
        List of trajectories in OpenAI chat completion format
    """
    spans = fetch_spans(base_url, limit)

    trajectories = []
    for span in spans:
        # Filter to LLM request spans
        if span.get("name") != "litellm_request":
            continue

        # Filter errors if requested
        if not include_errors and span.get("status_code") == "ERROR":
            continue

        # Only include spans with actual messages
        attrs = span.get("attributes", {})
        if not any(k.startswith("gen_ai.prompt.") for k in attrs):
            continue

        trajectory = extract_trajectory(span)

        if clean:
            trajectory = clean_trajectory(trajectory)

        # Only include if there are meaningful messages
        if trajectory["messages"]:
            trajectories.append(trajectory)

    return trajectories


def get_trajectory_by_trace_id(trace_id: str, base_url: str = "http://localhost:6006") -> dict | None:
    """
    Convenience function to get a single trajectory by trace ID.

    Args:
        trace_id: The trace ID to look up
        base_url: Phoenix server URL

    Returns:
        Trajectory dict or None if not found
    """
    trajectories = get_trajectories(base_url=base_url, limit=1000, include_errors=True, clean=True)
    for t in trajectories:
        if t["trace_id"] == trace_id:
            return t
    return None


def format_trajectory_as_text(trajectory: dict, include_thinking: bool = True) -> str:
    """
    Format a trajectory as human-readable text.

    Args:
        trajectory: The trajectory dict
        include_thinking: Whether to include agent thinking/reasoning

    Returns:
        Formatted string representation
    """
    lines = []
    lines.append(f"=== Trajectory: {trajectory['trace_id'][:12]}... ===")
    lines.append(f"Model: {trajectory['model']}")
    lines.append(f"Timestamp: {trajectory['timestamp']}")
    lines.append("")

    # Build a mapping of tool_call_id to tool name and arguments for reference
    tool_call_map = {}
    for msg in trajectory.get("messages", []):
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                tool_call_map[tc.get("id", "")] = {"name": func.get("name", "unknown"), "arguments": func.get("arguments", "{}")}

    for msg in trajectory.get("messages", []):
        role = msg.get("role", "unknown").upper()

        if role == "USER":
            lines.append("[USER]")
            lines.append(msg.get("content", ""))
            lines.append("")

        elif role == "ASSISTANT":
            lines.append("[ASSISTANT]")
            if include_thinking and msg.get("thinking"):
                lines.append("<thinking>")
                lines.append(msg["thinking"][:500] + "..." if len(msg.get("thinking", "")) > 500 else msg.get("thinking", ""))
                lines.append("</thinking>")
                lines.append("")
            if msg.get("content"):
                lines.append(msg["content"])
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "unknown")
                    tool_id = tc.get("id", "unknown")
                    lines.append(f"  -> Tool call: {tool_name} (id: {tool_id[:20]}...)")
                    args = func.get("arguments", "{}")
                    # Pretty print JSON arguments if possible
                    try:
                        args_obj = json.loads(args)
                        args = json.dumps(args_obj, indent=4)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    lines.append("     Arguments:")
                    for arg_line in args.split("\n"):
                        lines.append(f"       {arg_line}")
            lines.append("")

        elif role == "TOOL":
            tool_call_id = msg.get("tool_call_id", "unknown")
            tool_info = tool_call_map.get(tool_call_id, {})
            tool_name = tool_info.get("name", "unknown")
            lines.append(f"[TOOL RESULT] {tool_name} (id: {tool_call_id[:20]}...)")
            content = msg.get("content", "")
            # Try to pretty print JSON content
            try:
                content_obj = json.loads(content)
                content = json.dumps(content_obj, indent=2)
            except (json.JSONDecodeError, TypeError):
                pass
            lines.append("     Response:")
            for content_line in content.split("\n")[:50]:  # Limit to 50 lines
                lines.append(f"       {content_line}")
            if content.count("\n") > 50:
                lines.append(f"       ... (truncated, {content.count(chr(10)) - 50} more lines)")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract agent trajectories from Arize Phoenix")
    parser.add_argument("--url", default="http://localhost:6006", help="Phoenix server URL")
    parser.add_argument("--limit", type=int, default=100, help="Maximum number of spans to fetch")
    parser.add_argument("--include-errors", action="store_true", help="Include failed spans")
    parser.add_argument("--no-clean", action="store_true", help="Don't clean system reminders from messages")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="Pretty print JSON output")
    parser.add_argument("--trace-id", help="Filter to specific trace ID")
    parser.add_argument("--text", action="store_true", help="Output as human-readable text instead of JSON")

    args = parser.parse_args()

    trajectories = get_trajectories(base_url=args.url, limit=args.limit, include_errors=args.include_errors, clean=not args.no_clean)

    if args.trace_id:
        trajectories = [t for t in trajectories if t["trace_id"] == args.trace_id]

    # Sort by timestamp (most recent first)
    trajectories.sort(key=lambda t: t.get("timestamp", ""), reverse=True)

    if args.text:
        output = "\n\n".join(format_trajectory_as_text(t) for t in trajectories)
    else:
        output = json.dumps(trajectories, indent=2 if args.pretty else None, default=str)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Wrote {len(trajectories)} trajectories to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
