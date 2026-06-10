#!/usr/bin/env python3
# mypy: ignore-errors
# Exploration/reference code — not type-checked to the project standard.
"""Five-way comparison: empty / guidelines / skills / both / pruned.

Reads four metrics files:
  ../metrics/twobatch.metrics.jsonl          (twobatch — batch 1 = empty, batch 2 = guidelines)
  ../metrics/twobatch-skills.metrics.jsonl   (skills arm)
  ../metrics/twobatch-both.metrics.jsonl     (both arm)
  ../metrics/pruned-fixed-9atomic.metrics.jsonl   (pruned arm: skills + only no-skill-coverage atomics)
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

ARMS = ("empty", "guidelines", "skills", "both", "pruned")


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
    # Corrected pruned arm: re-run against a fixed (index-refreshed) wiki.
    # The original experiments/results-twobatch-pruned/ ran against a stale
    # index (0 skills exposed, 6 broken links) — see the Correction note.
    ap.add_argument("--pruned-metrics", default="../metrics/pruned-fixed-9atomic.metrics.jsonl")
    ap.add_argument("--out", default="experiments/twobatch-fiveway-comparison.md")
    args = ap.parse_args()

    rows: list[dict] = []
    for line in Path(args.twobatch_metrics).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r["arm"] = "empty" if r["batch"] == 1 else "guidelines"
        rows.append(r)
    for arm, path in (("skills", args.skills_metrics), ("both", args.both_metrics), ("pruned", args.pruned_metrics)):
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
    md.append("# Five-way wiki-helps comparison: empty / guidelines / skills / both / pruned")
    md.append("")
    md.append(
        "Same 16-task corpus, five arms, all `claude_md_strong` condition. "
        "Empty + guidelines arms are twobatch's batch-1 / batch-2. Skills arm "
        "is twobatch-skills (3 skills, no guidelines). Both arm is "
        "twobatch-both (those same 3 skills + ~15 atomics, no clusters). "
        "**Pruned arm** is twobatch-pruned: same 3 skills + only the "
        "no-skill-coverage atomics (delete-on-promote policy applied — "
        "image-format and CSV atomics archived because their corresponding "
        "skills were synthesized)."
    )
    md.append("")

    md.append("## Aggregate")
    md.append("")
    md.append("| Metric | Empty | Guidelines | Skills | Both | Pruned | P vs G | P vs S | P vs B |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
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
                f"| {label} | {vals['empty']} | {vals['guidelines']} | {vals['skills']} | "
                f"{vals['both']} | {vals['pruned']} | "
                f"{vals['pruned'] - vals['guidelines']:+d} | "
                f"{vals['pruned'] - vals['skills']:+d} | "
                f"{vals['pruned'] - vals['both']:+d} |"
            )
        else:
            md.append(
                f"| {label} | {fmt(vals['empty'], kind)} | {fmt(vals['guidelines'], kind)} | "
                f"{fmt(vals['skills'], kind)} | {fmt(vals['both'], kind)} | "
                f"{fmt(vals['pruned'], kind)} | "
                f"{delta(vals['guidelines'], vals['pruned'], kind)} | "
                f"{delta(vals['skills'], vals['pruned'], kind)} | "
                f"{delta(vals['both'], vals['pruned'], kind)} |"
            )
    md.append("")

    md.append("## By task family")
    md.append("")
    md.append("Median total_cost_usd. `Δ S→P` = `pruned` minus `skills`; `Δ B→P` = `pruned` minus `both`.")
    md.append("")
    md.append("| Family | Tasks | E acc | G acc | S acc | B acc | P acc | E $ | G $ | S $ | B $ | P $ | Δ S→P | Δ B→P |")
    md.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
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
            f"{fmt(acc(in_fam['pruned']), 'pct')} | "
            f"{fmt(cs['empty'], 'dollars')} | {fmt(cs['guidelines'], 'dollars')} | "
            f"{fmt(cs['skills'], 'dollars')} | {fmt(cs['both'], 'dollars')} | "
            f"{fmt(cs['pruned'], 'dollars')} | "
            f"{delta(cs['skills'], cs['pruned'], 'dollars')} | "
            f"{delta(cs['both'], cs['pruned'], 'dollars')} |"
        )
    md.append("")

    md.append("## Per task — cost USD")
    md.append("")
    md.append("| Task | E $ | G $ | S $ | B $ | P $ | Δ S→P | Δ B→P |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for tid in TASK_IDS_ORDER:
        if not by_task[tid]:
            continue
        cs = {a: median([r.get("total_cost_usd") for r in by_task[tid].get(a, [])]) for a in ARMS}
        md.append(
            f"| `{tid}` | {fmt(cs['empty'], 'dollars')} | {fmt(cs['guidelines'], 'dollars')} | "
            f"{fmt(cs['skills'], 'dollars')} | {fmt(cs['both'], 'dollars')} | "
            f"{fmt(cs['pruned'], 'dollars')} | "
            f"{delta(cs['skills'], cs['pruned'], 'dollars')} | "
            f"{delta(cs['both'], cs['pruned'], 'dollars')} |"
        )
    md.append("")

    md.append("## Per task — accuracy")
    md.append("")
    md.append("| Task | E acc | G acc | S acc | B acc | P acc |")
    md.append("|---|:-:|:-:|:-:|:-:|:-:|")
    for tid in TASK_IDS_ORDER:
        if not by_task[tid]:
            continue
        as_ = {a: acc(by_task[tid].get(a, [])) for a in ARMS}
        md.append(
            f"| `{tid}` | {fmt(as_['empty'], 'pct')} | {fmt(as_['guidelines'], 'pct')} | "
            f"{fmt(as_['skills'], 'pct')} | {fmt(as_['both'], 'pct')} | "
            f"{fmt(as_['pruned'], 'pct')} |"
        )
    md.append("")
    md.append("## Notes")
    md.append("")
    md.append("- Empty + guidelines + skills + both columns reproduce the 4-way comparison.")
    md.append(
        "- Pruned column is the new arm, testing the **delete-on-promote** policy: "
        "when `synthesize-skill` produces a skill, it inferentially archives the "
        "atomic guidelines covered by the skill (via tag-superset, slug-keyword, or "
        "format-identifier description match). Result: 3 skills + 9 atomics + 6 archived."
    )
    md.append(
        '- The pruned arm is the experimental answer to the open question "if '
        "'both' loses to 'skills-only', does 'skills + only the no-skill-coverage "
        "guidelines' beat 'skills-only'?\" raised in §7 of RESULTS-SUMMARY.md."
    )
    md.append("")
    md.append("### Correction — Pruned column is the re-run against a fixed index")
    md.append("")
    md.append(
        "The original pruned arm (commit `8bcd713`) ran against a wiki whose "
        "`_index.jsonl` was **stale**: `render-skill` archived the covered atomics "
        "but never refreshed the indexes, so the wiki exposed **0 skills, 15 "
        "guideline rows, 6 broken links**. Agents couldn't see the skills and fell "
        "back to dangling guideline rows (original: median $0.181, 290 output "
        "tokens, 3 wiki reads, 1 guideline read)."
    )
    md.append("")
    md.append(
        "Commit `2adc67a` fixed the builder to refresh the section indexes + "
        "`_index.jsonl` after `render-skill`/`render-cluster` (with an integrity "
        "assertion). This Pruned column is the full 16-task re-run against the "
        "corrected wiki: median **$0.173**, ~225 output tokens, 2 wiki reads, **0** "
        "guideline reads. Net: pruned moved from +1% to **-3% vs both** and from "
        "+24% to **+18% vs skills**. Skills-only is still cheapest, but the apparent "
        '"pruning is worse than both" result was largely the stale-index bug, not '
        "the policy. See `pruned-index-hypothesis.md` for the slice-level diagnosis."
    )
    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
