#!/usr/bin/env python3
"""Three-way comparison of empty / guidelines / skills arms on the same task corpus.

Reads:
  ../metrics/twobatch.metrics.jsonl          (twobatch — batch 1 = empty, batch 2 = guidelines)
  ../metrics/twobatch-skills.metrics.jsonl   (this experiment — skills arm)

Emits a markdown report with aggregate, per-family, and per-task tables.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

TASK_IDS_ORDER = [
    "t1-lens-model",
    "t6-png-dim", "t7-gif-dim", "t8-bmp-info", "t9-webp-dim",
    "t10-zip-list", "t11-tar-list", "t12-wav-info", "t13-gzip-dec",
    "t14-csv-quoted", "t15-jsonl-kinds", "t16-ini-key", "t17-log-errors",
    "t2-imports", "t3-todos", "t5-base64",
]

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


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def acc(rs):
    oms = [r.get("outcome_match") for r in rs if r.get("outcome_match") is not None]
    return sum(1 for x in oms if x) / len(oms) if oms else None


def fmt(x, kind="num"):
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


def delta(base, other, kind="num"):
    """other minus base. Sign in front; pct in parens vs base."""
    if base is None or other is None or base == 0:
        return "—"
    diff = other - base
    pct = diff / base
    sign = "+" if diff >= 0 else ""
    if kind == "tokens":
        return f"{sign}{int(diff):,} ({sign}{pct:.0%})"
    if kind == "duration":
        return f"{sign}{diff:.0f}s ({sign}{pct:.0%})"
    if kind == "dollars":
        return f"{sign}${diff:.4f} ({sign}{pct:.0%})"
    if kind == "pct":
        return f"{sign}{pct:.0%}"
    return f"{sign}{diff:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--twobatch-metrics", default="../metrics/twobatch.metrics.jsonl")
    ap.add_argument("--skills-metrics", default="../metrics/twobatch-skills.metrics.jsonl")
    ap.add_argument("--out", default="experiments/twobatch-skills-comparison.md")
    args = ap.parse_args()

    # Load: twobatch's batch 1 = empty arm; batch 2 = guidelines arm.
    rows: list[dict] = []
    for line in Path(args.twobatch_metrics).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r["arm"] = "empty" if r["batch"] == 1 else "guidelines"
        rows.append(r)
    # Skills arm: every row gets arm="skills".
    skills_path = Path(args.skills_metrics)
    if skills_path.exists():
        for line in skills_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            r["arm"] = "skills"
            rows.append(r)

    by_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_task[r["task"]][r["arm"]].append(r)

    md: list[str] = []
    md.append("# Three-way wiki-helps comparison: empty vs guidelines vs skills")
    md.append("")
    md.append("Same 16-task corpus, three arms, all `claude_md_strong` condition. "
              "Empty + guidelines arms are the existing twobatch experiment's "
              "batch-1 / batch-2. Skills arm is the new run against "
              "`wiki-twobatch-skills/`, populated from twobatch's batch-1 "
              "trajectories via `agent-wiki-synthesize-skill`.")
    md.append("")

    by_arm = {"empty": [r for r in rows if r["arm"] == "empty"],
              "guidelines": [r for r in rows if r["arm"] == "guidelines"],
              "skills": [r for r in rows if r["arm"] == "skills"]}

    md.append("## Aggregate (3 trials × 16 tasks per arm)")
    md.append("")
    md.append("| Metric | Empty | Guidelines | Skills | Skills vs guidelines |")
    md.append("|---|---:|---:|---:|---:|")
    pairs = [
        ("Trials", "len", "num"),
        ("Accuracy (mean)", "_acc", "pct"),
        ("Median duration", "duration_s", "duration"),
        ("Median input tokens", "input_tokens", "tokens"),
        ("Median output tokens", "output_tokens", "tokens"),
        ("Median total cost USD", "total_cost_usd", "dollars"),
        ("Median tool calls", "tool_calls", "num"),
        ("Median wiki reads", "wiki_reads_total", "num"),
        ("Median guideline reads", "guideline_reads", "num"),
    ]
    for label, field, kind in pairs:
        if field == "len":
            vals = {a: len(by_arm[a]) for a in ("empty", "guidelines", "skills")}
            md.append(f"| {label} | {vals['empty']} | {vals['guidelines']} | {vals['skills']} | "
                      f"{vals['skills']-vals['guidelines']:+d} |")
            continue
        if field == "_acc":
            vals = {a: acc(by_arm[a]) for a in ("empty", "guidelines", "skills")}
        else:
            vals = {a: median([r.get(field) for r in by_arm[a]]) for a in ("empty", "guidelines", "skills")}
        md.append(f"| {label} | {fmt(vals['empty'],kind)} | {fmt(vals['guidelines'],kind)} | "
                  f"{fmt(vals['skills'],kind)} | {delta(vals['guidelines'], vals['skills'], kind)} |")
    md.append("")

    md.append("## By task family")
    md.append("")
    md.append("Median per-trial within each family. Skills column shows Δ vs guidelines.")
    md.append("")
    md.append("| Family | Tasks | E acc | G acc | S acc | E dur | G dur | S dur | E tokens | G tokens | S tokens | E $ | G $ | S $ | Skills Δ$ |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    fam_groups: dict[str, list[str]] = defaultdict(list)
    for tid, fam in FAMILY.items():
        fam_groups[fam].append(tid)
    for fam, tids in fam_groups.items():
        in_fam = [r for r in rows if r["task"] in tids]
        e = [r for r in in_fam if r["arm"] == "empty"]
        g = [r for r in in_fam if r["arm"] == "guidelines"]
        s = [r for r in in_fam if r["arm"] == "skills"]
        md.append(
            f"| {fam} | {len(tids)} tasks | "
            f"{fmt(acc(e),'pct')} | {fmt(acc(g),'pct')} | {fmt(acc(s),'pct')} | "
            f"{fmt(median([r.get('duration_s') for r in e]),'duration')} | "
            f"{fmt(median([r.get('duration_s') for r in g]),'duration')} | "
            f"{fmt(median([r.get('duration_s') for r in s]),'duration')} | "
            f"{fmt(median([r.get('billable_tokens_proxy') for r in e]),'tokens')} | "
            f"{fmt(median([r.get('billable_tokens_proxy') for r in g]),'tokens')} | "
            f"{fmt(median([r.get('billable_tokens_proxy') for r in s]),'tokens')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in e]),'dollars')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in g]),'dollars')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in s]),'dollars')} | "
            f"{delta(median([r.get('total_cost_usd') for r in g]), median([r.get('total_cost_usd') for r in s]), 'dollars')} |"
        )
    md.append("")

    md.append("## Per task")
    md.append("")
    md.append("| Task | E acc | G acc | S acc | E dur | G dur | S dur | E $ | G $ | S $ | Skills Δ$ vs G |")
    md.append("|---|:-:|:-:|:-:|---:|---:|---:|---:|---:|---:|---:|")
    for tid in TASK_IDS_ORDER:
        e = by_task[tid].get("empty", [])
        g = by_task[tid].get("guidelines", [])
        s = by_task[tid].get("skills", [])
        if not (e or g or s):
            continue
        md.append(
            f"| `{tid}` | {fmt(acc(e),'pct')} | {fmt(acc(g),'pct')} | {fmt(acc(s),'pct')} | "
            f"{fmt(median([r.get('duration_s') for r in e]),'duration')} | "
            f"{fmt(median([r.get('duration_s') for r in g]),'duration')} | "
            f"{fmt(median([r.get('duration_s') for r in s]),'duration')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in e]),'dollars')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in g]),'dollars')} | "
            f"{fmt(median([r.get('total_cost_usd') for r in s]),'dollars')} | "
            f"{delta(median([r.get('total_cost_usd') for r in g]), median([r.get('total_cost_usd') for r in s]), 'dollars')} |"
        )
    md.append("")
    md.append("## Notes")
    md.append("")
    md.append("- Empty + guidelines columns reproduce the original twobatch comparison; "
              "skills column is new.")
    md.append("- 3 skills were synthesized from twobatch's batch-1 trajectories by the "
              "`agent-wiki-synthesize-skill` skill: `extract-jpeg-exif-camera-optics`, "
              "`read-image-format-dimensions`, `count-csv-rows-with-quoted-fields`. "
              "All other tasks in this arm have **no matching skill** — the agent "
              "should fall through to whatever it'd do on an empty wiki.")
    md.append("")
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
