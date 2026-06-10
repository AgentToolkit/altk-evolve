#!/usr/bin/env python3
# mypy: ignore-errors
# Exploration/reference code — not type-checked to the project standard.
"""Extract per-trial metrics from a stream-json transcript.

Pulls token counts from `assistant.usage` events + the terminal `result`
event. Counts tool calls and wiki-page reads. Used by the two-batch
experiment to build the with-wiki vs without-wiki comparison.

Usage:
    uv run python scripts/extract_trial_metrics.py \\
        --transcript path/to/trial-1.jsonl --task t6-png-dim --batch 1 \\
        --condition claude_md_strong [--outcome-match-all '...']

Emits one JSON object on stdout. Pipe to a .jsonl file for aggregation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse(transcript: Path) -> dict:
    events = [json.loads(ln) for ln in transcript.read_text(encoding="utf-8").splitlines() if ln.strip()]
    sid = "?"
    duration_ms = 0
    total_cost_usd = 0.0
    final_text = ""
    in_tokens = cache_creation = cache_read = out_tokens = 0
    tool_calls = 0
    wiki_reads = 0  # Read of AGENTS.md / _index.jsonl / guidelines/*.md
    agents_md_read = False
    index_read = False
    guideline_reads = 0

    for e in events:
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            sid = e.get("session_id") or sid
        elif t == "assistant":
            usage = (e.get("message") or {}).get("usage") or {}
            in_tokens += int(usage.get("input_tokens", 0) or 0)
            cache_creation += int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)
            out_tokens += int(usage.get("output_tokens", 0) or 0)
            for b in (e.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    final_text = b.get("text") or final_text
                elif b.get("type") == "tool_use":
                    tool_calls += 1
                    name = b.get("name")
                    inp = b.get("input") or {}
                    if name == "Read":
                        fp = inp.get("file_path", "")
                        if "AGENTS.md" in fp:
                            agents_md_read = True
                            wiki_reads += 1
                        elif "_index.jsonl" in fp:
                            index_read = True
                            wiki_reads += 1
                        elif "/guidelines/" in fp and fp.endswith(".md"):
                            guideline_reads += 1
                            wiki_reads += 1
                    elif name == "Bash":
                        cmd = inp.get("command", "") or ""
                        if "AGENTS.md" in cmd:
                            agents_md_read = True
                            wiki_reads += 1
                        if "_index.jsonl" in cmd:
                            index_read = True
                            wiki_reads += 1
                        m = re.search(r"/guidelines/[\w./-]+\.md", cmd)
                        if m:
                            guideline_reads += 1
                            wiki_reads += 1
        elif t == "result":
            duration_ms = int(e.get("duration_ms") or 0)
            total_cost_usd = float(e.get("total_cost_usd") or 0.0)
            final_text = e.get("result") or final_text

    return {
        "session_id": sid,
        "duration_s": round(duration_ms / 1000, 2),
        "total_cost_usd": total_cost_usd,
        "input_tokens": in_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": out_tokens,
        "billable_tokens_proxy": in_tokens + cache_creation + out_tokens,  # cache reads are cheap
        "tool_calls": tool_calls,
        "wiki_reads_total": wiki_reads,
        "agents_md_read": agents_md_read,
        "index_read": index_read,
        "guideline_reads": guideline_reads,
        "final_text_len": len(final_text or ""),
    }


def score_outcome(text: str, must_all: list[str]) -> bool:
    text_lc = (text or "").lower()
    return all(s.lower() in text_lc for s in must_all)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--condition", default="claude_md_strong")
    ap.add_argument("--trial", required=True)
    ap.add_argument("--outcome-match-all", default="", help="Comma-separated must-all-substrings for outcome_match")
    args = ap.parse_args()

    rec = parse(Path(args.transcript))
    rec["task"] = args.task
    rec["batch"] = int(args.batch)
    rec["condition"] = args.condition
    rec["trial"] = int(args.trial)

    must_all = [s.strip() for s in args.outcome_match_all.split(",") if s.strip()]
    # Re-parse the result event for outcome scoring
    final_text = ""
    for line in Path(args.transcript).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") == "result":
            final_text = e.get("result") or ""
            break
        if e.get("type") == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text":
                    final_text = b.get("text") or final_text
    rec["outcome_match"] = score_outcome(final_text, must_all) if must_all else None

    print(json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
