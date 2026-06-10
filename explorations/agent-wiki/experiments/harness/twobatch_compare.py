#!/usr/bin/env python3
"""Compare batch-1 (no wiki) and batch-2 (with wiki) metrics from
the two-batch experiment. Emits a markdown report.

Usage:
    uv run python scripts/twobatch_compare.py \\
        --metrics ../metrics/twobatch.metrics.jsonl \\
        --out experiments/twobatch-comparison.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FAMILY = {
    "t1-lens-model": "lens-model",
    "t6-png-dim": "image", "t7-gif-dim": "image",
    "t8-bmp-info": "image", "t9-webp-dim": "image",
    "t10-zip-list": "archive", "t11-tar-list": "archive",
    "t12-wav-info": "archive", "t13-gzip-dec": "archive",
    "t14-csv-quoted": "text", "t15-jsonl-kinds": "text",
    "t16-ini-key": "text", "t17-log-errors": "text",
    "t2-imports": "skip", "t3-todos": "skip", "t5-base64": "skip",
}


def median_or_none(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def mean_or_none(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def fmt(x: float | None, kind: str = "num") -> str:
    if x is None:
        return "—"
    if kind == "tokens":
        return f"{int(x):,}"
    if kind == "dollars":
        return f"${x:.4f}"
    if kind == "duration":
        return f"{x:.0f}s"
    if kind == "pct":
        return f"{x:.0%}"
    return f"{x:.1f}"


def delta_str(b1: float | None, b2: float | None, kind: str = "num") -> str:
    if b1 is None or b2 is None or b1 == 0:
        return "—"
    diff = b2 - b1
    pct = diff / b1
    sign = "+" if diff >= 0 else ""
    if kind == "tokens":
        return f"{sign}{int(diff):,} ({sign}{pct:.0%})"
    if kind == "duration":
        return f"{sign}{diff:.0f}s ({sign}{pct:.0%})"
    if kind == "dollars":
        return f"{sign}${diff:.4f} ({sign}{pct:.0%})"
    return f"{sign}{diff:.1f} ({sign}{pct:.0%})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    metrics_path = Path(args.metrics)
    out_path = Path(args.out)
    rows = [json.loads(l) for l in metrics_path.read_text().splitlines() if l.strip()]

    # by_task: {task_id: {1: [rows], 2: [rows]}}
    by_task: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_task[r["task"]][r["batch"]].append(r)

    # Per-task aggregates
    def agg(rs: list[dict], field: str, op=median_or_none) -> float | None:
        return op([r.get(field) for r in rs])

    def acc(rs: list[dict]) -> float | None:
        oms = [r.get("outcome_match") for r in rs if r.get("outcome_match") is not None]
        return (sum(1 for x in oms if x) / len(oms)) if oms else None

    md: list[str] = []
    md.append("# Two-batch wiki-helps comparison")
    md.append("")
    md.append("**Question**: does a populated wiki reduce token cost / wall-clock at "
              "equal-or-better accuracy, vs the same task on an empty wiki?")
    md.append("")
    md.append("Setup: 16 tasks × 3 trials × 2 batches = 96 sandbox trials, all "
              "`claude_md_strong`. Batch 1's agent saw an empty wiki. After ingestion "
              "the wiki was frozen. Batch 2's agent saw the populated wiki.")
    md.append("")

    # ── Aggregate ──
    all_b1 = [r for r in rows if r["batch"] == 1]
    all_b2 = [r for r in rows if r["batch"] == 2]
    md.append("## Aggregate (96 trials)")
    md.append("")
    md.append("| Metric | Batch 1 (empty wiki) | Batch 2 (with wiki) | Δ |")
    md.append("|---|---:|---:|---:|")
    pairs = [
        ("Trials", "len", "len", "num"),
        ("Accuracy (mean)", "outcome_match", "mean", "pct"),
        ("Median duration", "duration_s", "median", "duration"),
        ("Median input tokens", "input_tokens", "median", "tokens"),
        ("Median cache-creation tokens", "cache_creation_input_tokens", "median", "tokens"),
        ("Median cache-read tokens", "cache_read_input_tokens", "median", "tokens"),
        ("Median output tokens", "output_tokens", "median", "tokens"),
        ("Median billable proxy (in+cc+out)", "billable_tokens_proxy", "median", "tokens"),
        ("Median total cost USD", "total_cost_usd", "median", "dollars"),
        ("Median tool calls", "tool_calls", "median", "num"),
        ("Median wiki reads", "wiki_reads_total", "median", "num"),
        ("Median guideline reads", "guideline_reads", "median", "num"),
    ]
    for label, field, agg_op, kind in pairs:
        if field == "len":
            v1, v2 = len(all_b1), len(all_b2)
            md.append(f"| {label} | {v1} | {v2} | {v2-v1:+d} |")
            continue
        if agg_op == "mean":
            if field == "outcome_match":
                v1 = acc(all_b1); v2 = acc(all_b2)
            else:
                v1 = mean_or_none([r.get(field) for r in all_b1])
                v2 = mean_or_none([r.get(field) for r in all_b2])
        else:
            v1 = median_or_none([r.get(field) for r in all_b1])
            v2 = median_or_none([r.get(field) for r in all_b2])
        md.append(f"| {label} | {fmt(v1, kind)} | {fmt(v2, kind)} | {delta_str(v1, v2, kind)} |")
    md.append("")

    # ── By family ──
    md.append("## By task family")
    md.append("")
    md.append("Median per-trial cost within each family. Δ = batch-2 minus batch-1.")
    md.append("")
    md.append("| Family | Tasks | B1 acc | B2 acc | Δ acc | B1 dur | B2 dur | Δ dur | "
              "B1 tokens | B2 tokens | Δ tokens |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    fam_groups: dict[str, list[str]] = defaultdict(list)
    for tid, fam in FAMILY.items():
        fam_groups[fam].append(tid)
    for fam, tids in fam_groups.items():
        b1 = [r for r in rows if r["batch"] == 1 and r["task"] in tids]
        b2 = [r for r in rows if r["batch"] == 2 and r["task"] in tids]
        a1 = acc(b1); a2 = acc(b2)
        d1 = median_or_none([r.get("duration_s") for r in b1])
        d2 = median_or_none([r.get("duration_s") for r in b2])
        t1 = median_or_none([r.get("billable_tokens_proxy") for r in b1])
        t2 = median_or_none([r.get("billable_tokens_proxy") for r in b2])
        md.append(f"| {fam} | {', '.join(tids)} | {fmt(a1,'pct')} | {fmt(a2,'pct')} | "
                  f"{delta_str(a1,a2,'pct')} | {fmt(d1,'duration')} | {fmt(d2,'duration')} | "
                  f"{delta_str(d1,d2,'duration')} | {fmt(t1,'tokens')} | {fmt(t2,'tokens')} | "
                  f"{delta_str(t1,t2,'tokens')} |")
    md.append("")

    # ── Per task ──
    md.append("## Per task")
    md.append("")
    md.append("Median across 3 trials per cell. Token = `billable_tokens_proxy` "
              "(input + cache-creation + output; cache reads excluded).")
    md.append("")
    md.append("| Task | B1 acc | B2 acc | B1 dur | B2 dur | Δ dur | "
              "B1 tokens | B2 tokens | Δ tokens | B1 tools | B2 tools |")
    md.append("|---|:-:|:-:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tid in TASK_IDS_ORDER:
        b1 = by_task[tid].get(1, [])
        b2 = by_task[tid].get(2, [])
        if not b1 and not b2:
            continue
        a1 = acc(b1); a2 = acc(b2)
        d1 = median_or_none([r.get("duration_s") for r in b1])
        d2 = median_or_none([r.get("duration_s") for r in b2])
        t1 = median_or_none([r.get("billable_tokens_proxy") for r in b1])
        t2 = median_or_none([r.get("billable_tokens_proxy") for r in b2])
        tc1 = median_or_none([r.get("tool_calls") for r in b1])
        tc2 = median_or_none([r.get("tool_calls") for r in b2])
        md.append(f"| `{tid}` | {fmt(a1,'pct')} | {fmt(a2,'pct')} | "
                  f"{fmt(d1,'duration')} | {fmt(d2,'duration')} | {delta_str(d1,d2,'duration')} | "
                  f"{fmt(t1,'tokens')} | {fmt(t2,'tokens')} | {delta_str(t1,t2,'tokens')} | "
                  f"{fmt(tc1)} | {fmt(tc2)} |")
    md.append("")

    md.append("## Notes")
    md.append("")
    md.append("- `billable_tokens_proxy` = `input_tokens + cache_creation_input_tokens + output_tokens` "
              "(cache reads are very cheap and not directly billed at the same rate).")
    md.append("- A trial that timed out is recorded with `outcome_match=False`, "
              "`duration_s=300`, all token fields = 0. These bring batch-1 means down "
              "if they happen.")
    md.append("- Only `claude_md_strong` was run in this experiment for clean comparison "
              "(no condition mixing).")
    md.append("")

    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {out_path}", flush=True)
    return 0


TASK_IDS_ORDER = [
    "t1-lens-model",
    "t6-png-dim", "t7-gif-dim", "t8-bmp-info", "t9-webp-dim",
    "t10-zip-list", "t11-tar-list", "t12-wav-info", "t13-gzip-dec",
    "t14-csv-quoted", "t15-jsonl-kinds", "t16-ini-key", "t17-log-errors",
    "t2-imports", "t3-todos", "t5-base64",
]


if __name__ == "__main__":
    raise SystemExit(main())
