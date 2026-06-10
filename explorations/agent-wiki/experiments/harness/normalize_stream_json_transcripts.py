#!/usr/bin/env python3
"""Normalize `claude -p --output-format stream-json --verbose` outputs.

Reads stream-json transcripts emitted by the experiment runners and writes
one OpenAI-chat-completion JSON file per transcript, matching the schema
under trajectories/normalized/.

Usage:
    uv run python scripts/normalize_stream_json_transcripts.py \\
        --in  experiments/results/wiki-consult-20260605T153035Z/transcripts \\
        --out trajectories/normalized \\
        --label example-corpus \\
        --user-prompt "what lens model was used for @sample.jpg. use exif metadata" \\
        --trial-prefix wiki-consult
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_stream_json_file(path: Path, user_prompt: str) -> dict[str, Any]:
    """Parse one stream-json file into normalized form."""
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)
    result = next((e for e in events if e.get("type") == "result"), None)

    session_id = (init or {}).get("session_id") or path.stem
    model = (init or {}).get("model") or "claude-code"
    duration_ms = (result or {}).get("duration_ms") or 0

    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    tool_calls = 0
    tool_results = 0
    thinking = 0
    tool_counter: Counter[str] = Counter()
    in_tokens = cache_creation = cache_read = out_tokens = 0

    for ev in events:
        if ev.get("type") == "assistant":
            msg = ev.get("message", {}) or {}
            usage = msg.get("usage") or {}
            in_tokens += int(usage.get("input_tokens", 0) or 0)
            cache_creation += int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)
            out_tokens += int(usage.get("output_tokens", 0) or 0)
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    text = b.get("text", "")
                    if text:
                        messages.append({"role": "assistant", "content": text})
                elif t == "thinking":
                    thinking += 1
                elif t == "tool_use":
                    name = b.get("name", "")
                    tool_counter[name] += 1
                    tool_calls += 1
                    messages.append({
                        "role": "assistant",
                        "tool_calls": [{
                            "id": b.get("id"),
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(b.get("input") or {}),
                            },
                        }],
                    })
        elif ev.get("type") == "user":
            msg = ev.get("message", {}) or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    tool_results += 1
                    raw = b.get("content")
                    if isinstance(raw, list):
                        text_parts = [c.get("text", "") for c in raw if isinstance(c, dict) and c.get("type") == "text"]
                        text = "\n".join(text_parts)
                    elif isinstance(raw, str):
                        text = raw
                    else:
                        text = json.dumps(raw)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id"),
                        "content": text,
                    })

    top_tools = [{"tool": t, "count": c} for t, c in tool_counter.most_common(5)]

    started = (init or {}).get("session_start_time") or ""
    return {
        "schema_version": "1",
        "dataset": "claude-transcripts",
        "agent": "claude-code",
        "session_id": session_id,
        "model": model,
        "models": [model],
        "duration_seconds": round(duration_ms / 1000.0, 2),
        "stats": {
            "raw_event_count": len(events),
            "message_count": len(messages),
            "tool_call_count": tool_calls,
            "tool_result_count": tool_results,
            "thinking_block_count": thinking,
            "sidechain_count": 0,
            "top_tools": top_tools,
            "input_tokens": in_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": out_tokens,
            "total_cost_usd": float((result or {}).get("total_cost_usd") or 0.0),
        },
        "openai_chat_completion": {"messages": messages},
        "recalled_guidelines": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", required=True, help="Dir containing <condition>/trial-N.jsonl files (or a single file).")
    ap.add_argument("--out", default="trajectories/normalized", help="Output root.")
    ap.add_argument("--label", required=True, help="Label subdir under --out (becomes <out>/<label>/items/).")
    ap.add_argument("--user-prompt", required=True, help="The utt2 text the agent received (rebuilt as message[0]).")
    ap.add_argument("--trial-prefix", default="trial", help="Prefix used in trial_id.")
    args = ap.parse_args()

    in_root = Path(args.in_dir).resolve()
    out_root = Path(args.out).resolve() / args.label / "items"
    out_root.mkdir(parents=True, exist_ok=True)

    if in_root.is_file():
        files = [in_root]
    else:
        files = sorted(in_root.rglob("*.jsonl"))

    written = 0
    for f in files:
        rec = parse_stream_json_file(f, args.user_prompt)
        # condition is the parent directory name; trial id from filename
        condition = f.parent.name
        trial_name = f.stem  # 'trial-1', 'trial-2', etc.
        rec["trial_id"] = f"{args.trial_prefix}-{condition}-{trial_name}_{rec['session_id']}"
        rec["source"] = {
            "transcript_path": str(f.relative_to(Path.cwd())) if f.is_relative_to(Path.cwd()) else str(f),
            "session_id": rec["session_id"],
            "condition": condition,
            "trial": trial_name,
        }
        out_path = out_root / f"{condition}__{trial_name}__{rec['session_id']}.json"
        out_path.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        written += 1
        print(f"  wrote {out_path.relative_to(Path.cwd())}")
    print(f"normalized {written} transcript(s) → {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
