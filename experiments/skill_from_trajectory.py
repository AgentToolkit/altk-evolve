"""Experiment: skill-from-trajectory vs guidelines vs no-recall.

Per trial:
  1. Seed run on a fresh workspace (utterance 1) — produces guidelines via
     `learn` and a saved trajectory.
  2. Synthesis run on the same workspace — invokes the new
     `/evolve-lite:synthesize-skill` skill on the seed trajectory; produces
     `.evolve/skills/<name>/` and `.claude/skills/<name>/`.
  3. Branch into three measure conditions, each a fresh copy of demo/workspace
     plus the relevant memory:
       - no_recall: nothing
       - guidelines: seeded workspace's `.evolve/entities/` (no skills)
       - skill:     seeded workspace's `.claude/skills/` (no guidelines)
  4. For each condition, run each measure utterance once. Capture token usage,
     duration, and the skill the model invoked (if any).

Results: experiments/results/skill_from_trajectory_<UTC-timestamp>/
  - report.md            three-way × per-utterance comparison table
  - raw.json             full per-run usage payloads + tool-call summaries
  - synthesized_skills/  copy of each trial's synthesized skill dir

Usage:
    python3 experiments/skill_from_trajectory.py [--trials 5]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse helpers from the existing token-savings experiment.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_savings import (  # noqa: E402
    FORWARDED_ENV_VARS,
    REPO_ROOT,
    SANDBOX_IMAGE,
    SESSION_TIMEOUT_SECONDS,
    _check_prerequisites,
    _extract_usage as _extract_usage_base,
    _newest_transcript,
    _per_turn_usage,
)


def _extract_usage(parsed: dict | None) -> dict:
    """Extend the base extractor with total_cost_usd, which we report per-trial."""
    out = _extract_usage_base(parsed)
    if parsed is not None:
        out["total_cost_usd"] = parsed.get("total_cost_usd")
    return out


# All EXIF utterances, indexed by short key. The default seed is `gps` and the
# default measure set is all three; --seed-utterances can override the seed
# set (e.g. `gps,focal_length`) to test two-utterance seeding.
UTTERANCES: dict[str, str] = {
    "gps": "where was the photo @sample.jpg taken. use exif metadata",
    "focal_length": "what focal length was used to take the photo @sample.jpg. use exif metadata",
    "lens": "what lens model was used for @sample.jpg. use exif metadata",
}

# Default single-utterance seed (run A behavior).
DEFAULT_SEED_KEYS: list[str] = ["gps"]

# Default measure set (kept here for back-compat with the report-builder).
MEASURE_UTTERANCES: dict[str, str] = dict(UTTERANCES)

CONDITIONS = ("no_recall", "guidelines", "skill")


def _docker_path(p: Path) -> str:
    """Resolve a path for Docker bind-mounting on macOS.

    Docker on macOS doesn't follow the /tmp -> /private/tmp symlink for
    subdirectories: mounting /tmp/foo/bar lets the container see /tmp/foo
    but not its contents. Resolve to the real path before mounting.
    """
    return str(p.resolve())


def _run_sandbox_prompt_json(workspace: Path, prompt: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run a prompt with --output-format json and return (proc, parsed_json).

    Local copy of the helper from token_savings.py, but resolves the
    workspace path before binding (see _docker_path).
    """
    plugins = REPO_ROOT / "platform-integrations" / "claude" / "plugins"
    command = (
        "claude --plugin-dir /plugins/evolve-lite/ --dangerously-skip-permissions --output-format json -p "
        + shlex.quote(prompt)
    )
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e",
        "EVOLVE_DEBUG=1",
        "-v",
        f"{_docker_path(workspace)}:/workspace",
        "-v",
        f"{_docker_path(plugins)}:/plugins",
        SANDBOX_IMAGE,
        "bash",
        "-c",
        command,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)
    parsed: dict | None = None
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            for line in reversed(proc.stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
    return proc, parsed


def _fresh_workspace(tmp_root: Path, label: str) -> Path:
    """Copy demo/workspace into tmp_root/<label>, excluding .evolve/."""
    src = REPO_ROOT / "demo" / "workspace"
    dst = tmp_root / label
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".evolve", ".claude", "backup", "sandbox-backup"))
    return dst


def _copy_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _list_paths(root: Path, pattern: str) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob(pattern))


def _tool_calls_summary(transcript_path: Path | None) -> list[dict]:
    """Compact list of tool calls from a saved transcript: name + brief input."""
    if transcript_path is None or not transcript_path.is_file():
        return []
    out: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            brief = (
                inp.get("command")
                or inp.get("file_path")
                or inp.get("skill")
                or inp.get("path")
                or inp.get("pattern")
                or ""
            )
            if isinstance(brief, str) and len(brief) > 200:
                brief = brief[:197] + "..."
            out.append({"tool": block.get("name"), "brief": brief})
    return out


def _seed_and_synthesize(tmp_root: Path, trial_idx: int, seed_keys: list[str]) -> dict:
    """Run each seed utterance in turn, then the synthesize-skill skill.

    Synthesis runs against the *most recent* seed trajectory; the seed
    workspace's `.evolve/entities/` accumulates guidelines from every
    seed run (each one fires the learn Stop hook).
    """
    label = f"trial_{trial_idx}_seed"
    workspace = _fresh_workspace(tmp_root, label)

    seed_runs: list[dict] = []
    for n, key in enumerate(seed_keys, 1):
        utt = UTTERANCES[key]
        print(f"  [{label}] seed {n}/{len(seed_keys)} ({key})...", flush=True)
        t0 = time.time()
        proc, parsed = _run_sandbox_prompt_json(workspace, utt)
        print(f"  [{label}] seed {n} done in {time.time() - t0:.0f}s rc={proc.returncode}", flush=True)
        if proc.returncode != 0:
            return {
                "error": f"seed_failed_at_{n}",
                "stderr": proc.stderr[-1000:],
                "workspace": str(workspace),
            }
        seed_runs.append({"key": key, "usage": _extract_usage(parsed)})

    seed_transcript = _newest_transcript(workspace, exclude=set())
    if seed_transcript is None:
        return {"error": "seed_no_transcript", "workspace": str(workspace)}
    seed_traj_rel = "/".join(seed_transcript.relative_to(workspace).parts)

    print(f"  [{label}] synthesize-skill...", flush=True)
    synth_prompt = (
        f"Run /evolve-lite:synthesize-skill on the saved trajectory. "
        f"The saved trajectory path is: {seed_traj_rel}"
    )
    t1 = time.time()
    synth_proc, synth_parsed = _run_sandbox_prompt_json(workspace, synth_prompt)
    print(f"  [{label}] synth done in {time.time() - t1:.0f}s rc={synth_proc.returncode}", flush=True)

    skills = _list_paths(workspace / ".evolve" / "skills", "SKILL.md")
    skill_names = sorted({Path(p).parent.parts[0] for p in skills if Path(p).name == "SKILL.md"})

    # Aggregate seed_usage across the per-utterance runs so the report's
    # "seed total" column has a single number when run A is replayed.
    aggregate_seed_usage = {}
    if seed_runs:
        last = seed_runs[-1]["usage"]  # noqa: F841
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "total_tokens"):
            vals = [r["usage"].get(k) for r in seed_runs if isinstance(r["usage"].get(k), (int, float))]
            aggregate_seed_usage[k] = sum(vals) if vals else None
        costs = [r["usage"].get("total_cost_usd") for r in seed_runs if isinstance(r["usage"].get("total_cost_usd"), (int, float))]
        aggregate_seed_usage["total_cost_usd"] = sum(costs) if costs else None
        turns = [r["usage"].get("num_turns") for r in seed_runs if isinstance(r["usage"].get("num_turns"), (int, float))]
        aggregate_seed_usage["num_turns"] = sum(turns) if turns else None

    return {
        "workspace": str(workspace),
        "seed_keys": seed_keys,
        "per_seed_runs": seed_runs,
        "seed_trajectory": seed_traj_rel,
        "seed_usage": aggregate_seed_usage,
        "synth_usage": _extract_usage(synth_parsed),
        "synth_returncode": synth_proc.returncode,
        "synth_stderr_tail": synth_proc.stderr[-500:] if synth_proc.returncode != 0 else "",
        "skills_synthesized": skill_names,
        "guideline_count": len(_list_paths(workspace / ".evolve" / "entities", "*.md")),
    }


def _build_condition_workspace(seed_workspace: Path, tmp_root: Path, trial_idx: int, condition: str) -> Path:
    """Branch a fresh measure workspace from the seeded one for a given condition."""
    label = f"trial_{trial_idx}_{condition}"
    dst = _fresh_workspace(tmp_root, label)
    if condition == "no_recall":
        return dst
    # Both guidelines and skill conditions need a writable .evolve/ for recall
    # hooks to function (and to write audit + new trajectories).
    (dst / ".evolve").mkdir(exist_ok=True)
    if condition == "guidelines":
        src_entities = seed_workspace / ".evolve" / "entities"
        if src_entities.is_dir():
            _copy_dir(src_entities, dst / ".evolve" / "entities")
    elif condition == "skill":
        src_claude = seed_workspace / ".claude"
        if src_claude.is_dir():
            _copy_dir(src_claude, dst / ".claude")
        src_evolve_skills = seed_workspace / ".evolve" / "skills"
        if src_evolve_skills.is_dir():
            _copy_dir(src_evolve_skills, dst / ".evolve" / "skills")
    return dst


def _do_measure_run(
    workspace: Path,
    utterance: str,
    label: str,
) -> dict:
    print(f"  [{label}] measure...", flush=True)
    t0 = time.time()
    pre_transcripts = (
        set((workspace / ".evolve" / "trajectories").glob("*.jsonl"))
        if (workspace / ".evolve" / "trajectories").is_dir()
        else set()
    )
    proc, parsed = _run_sandbox_prompt_json(workspace, utterance)
    print(f"  [{label}] done in {time.time() - t0:.0f}s rc={proc.returncode}", flush=True)
    if proc.returncode != 0:
        return {"label": label, "error": "measure_failed", "stderr": proc.stderr[-500:]}

    transcript = _newest_transcript(workspace, exclude=pre_transcripts)
    return {
        "label": label,
        "headline_usage": _extract_usage(parsed),
        "raw_json": parsed,
        "per_turn": _per_turn_usage(transcript) if transcript else [],
        "tool_calls": _tool_calls_summary(transcript),
        "transcript_path": str(transcript) if transcript else None,
    }


def _summarize(values: list[float]) -> dict:
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _format_table(results: dict, utterance_keys: list[str] | None = None) -> str:
    """Build a markdown table: rows = (utterance, metric); cols = conditions."""
    lines = []
    metrics = [
        ("total_tokens", "total"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_create"),
        ("duration_ms", "duration_ms"),
        ("num_turns", "num_turns"),
    ]
    keys = utterance_keys if utterance_keys is not None else list(MEASURE_UTTERANCES.keys())
    for utt_key in keys:
        lines.append(f"\n### Utterance: `{utt_key}`")
        lines.append("")
        header = "| metric | " + " | ".join(c for c in CONDITIONS) + " |"
        sep = "| --- " + "| --- " * len(CONDITIONS) + "|"
        lines.append(header)
        lines.append(sep)
        for key, label in metrics:
            row = [label]
            for cond in CONDITIONS:
                runs = results.get(cond, {}).get(utt_key, [])
                if key == "num_turns":
                    vals = [r.get("raw_json", {}).get("num_turns") for r in runs if "raw_json" in r]
                else:
                    vals = [r.get("headline_usage", {}).get(key) for r in runs if "headline_usage" in r]
                summary = _summarize([v for v in vals if v is not None])
                if not summary.get("n"):
                    row.append("n/a")
                else:
                    row.append(f"{summary['mean']:.0f} ({summary['min']:.0f}–{summary['max']:.0f})")
            lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _format_synth_costs(seeds: list[dict]) -> str:
    rows = [
        "| trial | seed total | synth total | synth turns | synth $ | skills | guidelines |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, seed in enumerate(seeds, 1):
        if "error" in seed:
            rows.append(f"| {i} | error: {seed['error']} | | | | | |")
            continue
        s = seed.get("seed_usage") or {}
        sy = seed.get("synth_usage") or {}
        cost = sy.get("total_cost_usd")
        cost_str = f"${cost:.3f}" if isinstance(cost, (int, float)) else "?"
        rows.append(
            f"| {i} | {s.get('total_tokens', '?')} | {sy.get('total_tokens', '?')} | "
            f"{sy.get('num_turns', '?')} | {cost_str} | "
            f"{', '.join(seed.get('skills_synthesized', []))} | "
            f"{seed.get('guideline_count', '?')} |"
        )
    return "\n".join(rows)


def _write_report(
    results_dir: Path,
    seeds: list[dict],
    results: dict,
    utterance_keys: list[str],
    seed_keys: list[str],
) -> Path:
    lines = ["# Skill-from-trajectory experiment\n"]
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    if len(seed_keys) == 1:
        lines.append(f"**Seed utterance** (`{seed_keys[0]}`): `{UTTERANCES[seed_keys[0]]}`")
    else:
        lines.append("**Seed utterances** (run sequentially in the seed workspace before synthesis):")
        for k in seed_keys:
            lines.append(f"- `{k}`: `{UTTERANCES[k]}`")
    lines.append("\n**Conditions:**")
    lines.append("- `no_recall` — fresh `demo/workspace`, no `.evolve/`, no `.claude/skills/`")
    lines.append("- `guidelines` — fresh `demo/workspace` + seeded `.evolve/entities/`")
    lines.append("- `skill` — fresh `demo/workspace` + seeded `.claude/skills/` and `.evolve/skills/`\n")
    lines.append("**Measure utterances:**")
    for k in utterance_keys:
        lines.append(f"- `{k}`: `{UTTERANCES[k]}`")
    lines.append("")
    lines.append("## Synthesis cost (per-trial setup, NOT included in any condition)\n")
    lines.append(_format_synth_costs(seeds))
    lines.append("\n## Comparison\n")
    lines.append(
        "Mean (range) across trials. `total` is the unweighted sum of input + output + cache_read + cache_create — "
        "cache_read is ~10x cheaper per token than fresh input, so this overweights cache."
    )
    lines.append(_format_table(results, utterance_keys))
    path = results_dir / "report.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _save_synthesized_skills(seeds: list[dict], results_dir: Path) -> None:
    out_root = results_dir / "synthesized_skills"
    out_root.mkdir(parents=True, exist_ok=True)
    for i, seed in enumerate(seeds, 1):
        ws = seed.get("workspace")
        if not ws:
            continue
        skills_dir = Path(ws) / ".evolve" / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            dst = out_root / f"trial_{i}_{skill_dir.name}"
            _copy_dir(skill_dir, dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=5, help="trials per condition (default 5)")
    parser.add_argument(
        "--seed-utterances",
        nargs="+",
        choices=list(UTTERANCES.keys()),
        default=DEFAULT_SEED_KEYS,
        help="utterance keys to seed with, run sequentially (default: gps)",
    )
    parser.add_argument(
        "--utterances",
        nargs="+",
        choices=list(UTTERANCES.keys()),
        default=None,
        help=(
            "which measure utterances to run. Default: when --seed-utterances is the default (gps), "
            "measures all 3; when --seed-utterances is overridden, measures only the keys NOT in the seed set."
        ),
    )
    parser.add_argument("--keep-workspaces", action="store_true", help="don't delete per-trial workspaces")
    args = parser.parse_args()

    seed_keys: list[str] = list(args.seed_utterances)
    if args.utterances is None:
        if seed_keys == DEFAULT_SEED_KEYS:
            measure_keys = list(UTTERANCES.keys())
        else:
            measure_keys = [k for k in UTTERANCES.keys() if k not in seed_keys]
            if not measure_keys:
                parser.error("seed set covers every utterance — no measure utterances left; pass --utterances explicitly")
    else:
        measure_keys = list(args.utterances)

    _check_prerequisites()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_dir = REPO_ROOT / "experiments" / "results" / f"skill_from_trajectory_{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = results_dir / "workspaces"
    workspace_root.mkdir(exist_ok=True)
    print(f"Results dir: {results_dir}")
    print(f"Trials: {args.trials}")
    print(f"Seed utterances: {seed_keys}")
    print(f"Measure utterances: {measure_keys}")

    seeds: list[dict] = []
    results: dict = {cond: {u: [] for u in measure_keys} for cond in CONDITIONS}

    for i in range(1, args.trials + 1):
        print(f"\n=== trial {i}/{args.trials}: seed + synthesize ===")
        seed = _seed_and_synthesize(workspace_root, i, seed_keys)
        seeds.append(seed)
        # Persist progressively in case we crash mid-run.
        (results_dir / "raw.json").write_text(
            json.dumps({"seeds": seeds, "results": results}, indent=2, default=str)
        )
        if "error" in seed:
            print(f"  [trial {i}] seed/synth FAILED: {seed['error']} — skipping measure runs")
            continue
        if not seed.get("skills_synthesized"):
            print(f"  [trial {i}] synthesize produced NO skill — skipping measure runs")
            continue
        seed_workspace = Path(seed["workspace"])

        for cond in CONDITIONS:
            cond_workspace = _build_condition_workspace(seed_workspace, workspace_root, i, cond)
            for utt_key in measure_keys:
                utt_text = UTTERANCES[utt_key]
                label = f"trial_{i}_{cond}_{utt_key}"
                run_result = _do_measure_run(cond_workspace, utt_text, label)
                run_result["condition"] = cond
                run_result["utterance"] = utt_key
                run_result["trial"] = i
                results[cond][utt_key].append(run_result)
                (results_dir / "raw.json").write_text(
                    json.dumps({"seeds": seeds, "results": results}, indent=2, default=str)
                )

    _save_synthesized_skills(seeds, results_dir)
    report_path = _write_report(results_dir, seeds, results, measure_keys, seed_keys)

    print("\n" + "=" * 60)
    print(_format_table(results, measure_keys))
    print("=" * 60)
    print(f"\nReport: {report_path}")
    print(f"Raw:    {results_dir / 'raw.json'}")

    if not args.keep_workspaces:
        shutil.rmtree(workspace_root, ignore_errors=True)

    errors = [s for s in seeds if "error" in s] + [
        r
        for cond in CONDITIONS
        for u in measure_keys
        for r in results[cond][u]
        if "error" in r
    ]
    if errors:
        print(f"\n{len(errors)} run(s) had errors — see raw.json")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
