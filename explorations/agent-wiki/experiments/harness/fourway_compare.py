#!/usr/bin/env python3
# mypy: ignore-errors
# Exploration/reference code — not type-checked to the project standard.
"""Four-way comparison: empty / guidelines / skills / both.

Reads three metrics files:
  ../metrics/twobatch.metrics.jsonl          (twobatch — batch 1 = empty, batch 2 = guidelines)
  ../metrics/twobatch-skills.metrics.jsonl   (skills arm)
  ../metrics/twobatch-both.metrics.jsonl     (both arm)
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
    "t6-png-dim",
    "t7-gif-dim",
    "t8-bmp-info",
    "t9-webp-dim",
    "t10-zip-list",
    "t11-tar-list",
    "t12-wav-info",
    "t13-gzip-dec",
    "t14-csv-quoted",
    "t15-jsonl-kinds",
    "t16-ini-key",
    "t17-log-errors",
    "t2-imports",
    "t3-todos",
    "t5-base64",
]

FAMILY = {
    "t1-lens-model": "lens-model",
    "t6-png-dim": "image",
    "t7-gif-dim": "image",
    "t8-bmp-info": "image",
    "t9-webp-dim": "image",
    "t10-zip-list": "archive",
    "t11-tar-list": "archive",
    "t12-wav-info": "archive",
    "t13-gzip-dec": "archive",
    "t14-csv-quoted": "text",
    "t15-jsonl-kinds": "text",
    "t16-ini-key": "text",
    "t17-log-errors": "text",
    "t2-imports": "skip",
    "t3-todos": "skip",
    "t5-base64": "skip",
}

ARMS = ("empty", "guidelines", "skills", "both")


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
    ap.add_argument("--both-metrics", default="../metrics/twobatch-both.metrics.jsonl")
    ap.add_argument("--out", default="experiments/twobatch-fourway-comparison.md")
    args = ap.parse_args()

    rows: list[dict] = []
    for line in Path(args.twobatch_metrics).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r["arm"] = "empty" if r["batch"] == 1 else "guidelines"
        rows.append(r)
    for arm, path in (("skills", args.skills_metrics), ("both", args.both_metrics)):
        p = Path(path)
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            r["arm"] = arm
            rows.append(r)

    by_task: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_task[r["task"]][r["arm"]].append(r)

    by_arm = {a: [r for r in rows if r["arm"] == a] for a in ARMS}

    md: list[str] = []
    md.append("# Four-way wiki-helps comparison: empty / guidelines / skills / both")
    md.append("")
    md.append(
        "Same 16-task corpus, four arms, all `claude_md_strong` condition. "
        "Empty + guidelines arms are twobatch's batch-1 / batch-2. Skills arm "
        "is twobatch-skills (3 skills, no guidelines). Both arm is "
        "twobatch-both (those same 3 skills + ~15 atomics, no clusters)."
    )
    md.append("")

    md.append("## Aggregate")
    md.append("")
    md.append("| Metric | Empty | Guidelines | Skills | Both | Both vs G | Both vs S |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
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
        vals = {}
        for a in ARMS:
            arm_rows = by_arm[a]
            if field == "len":
                vals[a] = len(arm_rows)
            elif field == "_acc":
                vals[a] = acc(arm_rows)
            else:
                vals[a] = median([r.get(field) for r in arm_rows])
        if field == "len":
            md.append(
                f"| {label} | {vals['empty']} | {vals['guidelines']} | {vals['skills']} | {vals['both']} | "
                f"{vals['both'] - vals['guidelines']:+d} | {vals['both'] - vals['skills']:+d} |"
            )
        else:
            md.append(
                f"| {label} | {fmt(vals['empty'], kind)} | {fmt(vals['guidelines'], kind)} | "
                f"{fmt(vals['skills'], kind)} | {fmt(vals['both'], kind)} | "
                f"{delta(vals['guidelines'], vals['both'], kind)} | "
                f"{delta(vals['skills'], vals['both'], kind)} |"
            )
    md.append("")

    md.append("## By task family")
    md.append("")
    md.append("Median total_cost_usd. `Δ G→B` is `both` minus `guidelines`; `Δ S→B` is `both` minus `skills`.")
    md.append("")
    md.append("| Family | Tasks | E acc | G acc | S acc | B acc | E $ | G $ | S $ | B $ | Δ G→B | Δ S→B |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    fam_groups: dict[str, list[str]] = defaultdict(list)
    for tid, fam in FAMILY.items():
        fam_groups[fam].append(tid)
    for fam, tids in fam_groups.items():
        in_fam = {a: [r for r in rows if r["task"] in tids and r["arm"] == a] for a in ARMS}
        cs = {a: median([r.get("total_cost_usd") for r in in_fam[a]]) for a in ARMS}
        md.append(
            f"| {fam} | {len(tids)} | "
            f"{fmt(acc(in_fam['empty']), 'pct')} | {fmt(acc(in_fam['guidelines']), 'pct')} | "
            f"{fmt(acc(in_fam['skills']), 'pct')} | {fmt(acc(in_fam['both']), 'pct')} | "
            f"{fmt(cs['empty'], 'dollars')} | {fmt(cs['guidelines'], 'dollars')} | "
            f"{fmt(cs['skills'], 'dollars')} | {fmt(cs['both'], 'dollars')} | "
            f"{delta(cs['guidelines'], cs['both'], 'dollars')} | "
            f"{delta(cs['skills'], cs['both'], 'dollars')} |"
        )
    md.append("")

    md.append("## Per task — cost USD")
    md.append("")
    md.append("| Task | E $ | G $ | S $ | B $ | Δ G→B | Δ S→B |")
    md.append("|---|---:|---:|---:|---:|---:|---:|")
    for tid in TASK_IDS_ORDER:
        if not by_task[tid]:
            continue
        cs = {a: median([r.get("total_cost_usd") for r in by_task[tid].get(a, [])]) for a in ARMS}
        md.append(
            f"| `{tid}` | {fmt(cs['empty'], 'dollars')} | {fmt(cs['guidelines'], 'dollars')} | "
            f"{fmt(cs['skills'], 'dollars')} | {fmt(cs['both'], 'dollars')} | "
            f"{delta(cs['guidelines'], cs['both'], 'dollars')} | "
            f"{delta(cs['skills'], cs['both'], 'dollars')} |"
        )
    md.append("")

    md.append("## Per task — accuracy")
    md.append("")
    md.append("| Task | E acc | G acc | S acc | B acc |")
    md.append("|---|:-:|:-:|:-:|:-:|")
    for tid in TASK_IDS_ORDER:
        if not by_task[tid]:
            continue
        as_ = {a: acc(by_task[tid].get(a, [])) for a in ARMS}
        md.append(
            f"| `{tid}` | {fmt(as_['empty'], 'pct')} | {fmt(as_['guidelines'], 'pct')} | "
            f"{fmt(as_['skills'], 'pct')} | {fmt(as_['both'], 'pct')} |"
        )
    md.append("")
    md.append("## Notes")
    md.append("")
    md.append("- Empty + guidelines columns reproduce twobatch.")
    md.append("- Skills column reproduces the skills-arm experiment.")
    md.append(
        "- Both column is the new arm: same 3 skills + ~15 atomics from "
        "twobatch's batch-1 trajectories. No clusters (matching the "
        "guidelines arm's structure)."
    )
    md.append("- Trivial-recipe tasks (t11-tar, t13-gzip, t15-jsonl, t16-ini, t17-log, t2/t3, t5) have no matching skill in any arm.")
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
