"""Experiment: measure token savings from recalled guidelines.

Adapted from test_claude_sandbox_learn_recall.py. Not a pytest test — runs as
a script and prints a comparison table.

Design:
  - Seed run: utterance 1 ("where was the photo @sample.jpg taken. use exif
    metadata") on a fresh demo/workspace copy. Produces .evolve/entities/.
  - With-guidelines run: utterance 2 ("what focal length...") on the same
    workspace. Recall hook injects the guideline.
  - Without-guidelines run: utterance 2 on a NEW fresh workspace copy with no
    .evolve/. Recall has nothing to find.

Repeat N times per condition. Reports headline tokens from claude
--output-format json and per-turn usage parsed from the saved transcript.

Usage:
    python experiments/token_savings.py [--runs 3]
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

# Constants mirrored from test_claude_sandbox_learn_recall.py — kept inline
# so this script doesn't pull in pytest just to import them.
SANDBOX_IMAGE = "claude-sandbox"
REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_TIMEOUT_SECONDS = 600
FORWARDED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_MODEL",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
)


UTTERANCE_LEARN = "where was the photo @sample.jpg taken. use exif metadata"
UTTERANCE_MEASURE = "what focal length was used to take the photo @sample.jpg. use exif metadata"


def _check_prerequisites() -> None:
    if shutil.which("docker") is None:
        sys.exit("ERROR: docker not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        sys.exit("ERROR: docker daemon not running")
    if subprocess.run(["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True).returncode != 0:
        sys.exit(f"ERROR: sandbox image {SANDBOX_IMAGE!r} not built — run `just sandbox-build claude`")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("ERROR: ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) not set")


def _fresh_workspace(tmp_root: Path, label: str) -> Path:
    src = REPO_ROOT / "demo" / "workspace"
    dst = tmp_root / label
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))
    return dst


def _run_sandbox_prompt_json(workspace: Path, prompt: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run a prompt with --output-format json and return (proc, parsed_json)."""
    plugins = REPO_ROOT / "platform-integrations" / "claude" / "plugins"
    command = (
        "claude --plugin-dir /plugins/evolve-lite/ --dangerously-skip-permissions "
        "--output-format json -p " + shlex.quote(prompt)
    )
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    cmd += [
        "-e", "EVOLVE_DEBUG=1",
        "-v", f"{workspace}:/workspace",
        "-v", f"{plugins}:/plugins",
        SANDBOX_IMAGE,
        "bash", "-c", command,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SESSION_TIMEOUT_SECONDS)
    parsed: dict | None = None
    if proc.returncode == 0 and proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # Output may be a stream of multiple JSON objects on rare occasions;
            # fall back to last well-formed line.
            for line in reversed(proc.stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        parsed = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
    return proc, parsed


def _per_turn_usage(transcript_path: Path) -> list[dict]:
    """Pull the usage block from each assistant message in the transcript."""
    turns: list[dict] = []
    for line in transcript_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        turns.append({
            "type": record.get("type"),
            "role": message.get("role"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        })
    return turns


def _newest_transcript(workspace: Path, exclude: set[Path]) -> Path | None:
    trajectories_dir = workspace / ".evolve" / "trajectories"
    if not trajectories_dir.is_dir():
        return None
    candidates = [p for p in trajectories_dir.glob("*.jsonl") if p not in exclude]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _list_entities(workspace: Path) -> list[str]:
    entities_dir = workspace / ".evolve" / "entities"
    if not entities_dir.is_dir():
        return []
    return sorted(str(p.relative_to(entities_dir)) for p in entities_dir.rglob("*.md"))


def _extract_usage(parsed: dict | None) -> dict:
    """Pull the headline usage block out of `claude --output-format json`."""
    if not parsed:
        return {}
    usage = parsed.get("usage") or {}
    # claude reports cumulative usage in `usage`. Some versions also provide
    # input_tokens/output_tokens at the top level; prefer the explicit block.
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "total_tokens": (
            (usage.get("input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
        ),
        "duration_ms": parsed.get("duration_ms"),
        "num_turns": parsed.get("num_turns"),
    }


def _do_with_guidelines_run(tmp_root: Path, idx: int) -> dict:
    label = f"with_guidelines_{idx}"
    workspace = _fresh_workspace(tmp_root, label)

    print(f"  [{label}] seeding (utterance 1)...", flush=True)
    t0 = time.time()
    seed_proc, _seed_parsed = _run_sandbox_prompt_json(workspace, UTTERANCE_LEARN)
    print(f"  [{label}] seed done in {time.time()-t0:.0f}s rc={seed_proc.returncode}", flush=True)
    if seed_proc.returncode != 0:
        return {"label": label, "error": "seed_failed", "stderr": seed_proc.stderr[-1000:]}

    seed_transcripts = set((workspace / ".evolve" / "trajectories").glob("*.jsonl")) if (workspace / ".evolve" / "trajectories").is_dir() else set()
    entities = _list_entities(workspace)
    if not entities:
        return {"label": label, "error": "no_guideline_learned", "stdout": seed_proc.stdout[-1000:]}

    print(f"  [{label}] measure (utterance 2) — {len(entities)} guideline(s) recallable...", flush=True)
    t1 = time.time()
    proc, parsed = _run_sandbox_prompt_json(workspace, UTTERANCE_MEASURE)
    print(f"  [{label}] measure done in {time.time()-t1:.0f}s rc={proc.returncode}", flush=True)
    if proc.returncode != 0:
        return {"label": label, "error": "measure_failed", "stderr": proc.stderr[-1000:]}

    transcript = _newest_transcript(workspace, exclude=seed_transcripts)
    return {
        "label": label,
        "condition": "with_guidelines",
        "headline_usage": _extract_usage(parsed),
        "raw_json": parsed,
        "per_turn": _per_turn_usage(transcript) if transcript else [],
        "transcript_path": str(transcript) if transcript else None,
        "entities_seeded": entities,
    }


def _do_shared_seed_runs(tmp_root: Path, n_runs: int) -> list[dict]:
    """Seed once, then measure n_runs times against the same workspace.

    Recall is driven by `.evolve/entities/`, which doesn't change across the
    measure runs, so all measure runs see the same recallable guidelines.
    `.evolve/trajectories/` and `.evolve/audit.log` accumulate as they would
    in normal day-to-day use of the same project.
    """
    label_root = "with_guidelines_shared"
    workspace = _fresh_workspace(tmp_root, label_root)

    print(f"  [{label_root}] seeding (utterance 1)...", flush=True)
    t0 = time.time()
    seed_proc, _seed_parsed = _run_sandbox_prompt_json(workspace, UTTERANCE_LEARN)
    print(f"  [{label_root}] seed done in {time.time()-t0:.0f}s rc={seed_proc.returncode}", flush=True)
    if seed_proc.returncode != 0:
        return [{"label": label_root, "error": "seed_failed", "stderr": seed_proc.stderr[-1000:]}]

    entities = _list_entities(workspace)
    if not entities:
        return [{"label": label_root, "error": "no_guideline_learned", "stdout": seed_proc.stdout[-1000:]}]

    results: list[dict] = []
    for i in range(1, n_runs + 1):
        label = f"{label_root}_{i}"
        # Snapshot trajectories present BEFORE this measure run, so we can find
        # the new transcript afterward.
        trajectories_dir = workspace / ".evolve" / "trajectories"
        prior_transcripts = set(trajectories_dir.glob("*.jsonl")) if trajectories_dir.is_dir() else set()

        print(f"  [{label}] measure (utterance 2) — {len(entities)} guideline(s) recallable...", flush=True)
        t1 = time.time()
        proc, parsed = _run_sandbox_prompt_json(workspace, UTTERANCE_MEASURE)
        print(f"  [{label}] measure done in {time.time()-t1:.0f}s rc={proc.returncode}", flush=True)
        if proc.returncode != 0:
            results.append({"label": label, "error": "measure_failed", "stderr": proc.stderr[-1000:]})
            continue

        transcript = _newest_transcript(workspace, exclude=prior_transcripts)
        results.append({
            "label": label,
            "condition": "with_guidelines",
            "headline_usage": _extract_usage(parsed),
            "raw_json": parsed,
            "per_turn": _per_turn_usage(transcript) if transcript else [],
            "transcript_path": str(transcript) if transcript else None,
            "entities_seeded": entities,
        })
    return results


def _do_without_guidelines_run(tmp_root: Path, idx: int) -> dict:
    label = f"without_guidelines_{idx}"
    workspace = _fresh_workspace(tmp_root, label)
    print(f"  [{label}] measure (utterance 2) — no .evolve/ ...", flush=True)
    t0 = time.time()
    proc, parsed = _run_sandbox_prompt_json(workspace, UTTERANCE_MEASURE)
    print(f"  [{label}] done in {time.time()-t0:.0f}s rc={proc.returncode}", flush=True)
    if proc.returncode != 0:
        return {"label": label, "error": "measure_failed", "stderr": proc.stderr[-1000:]}

    transcript = _newest_transcript(workspace, exclude=set())
    return {
        "label": label,
        "condition": "without_guidelines",
        "headline_usage": _extract_usage(parsed),
        "raw_json": parsed,
        "per_turn": _per_turn_usage(transcript) if transcript else [],
        "transcript_path": str(transcript) if transcript else None,
    }


def _summarize(runs: list[dict], key: str) -> dict:
    values = [r["headline_usage"].get(key) for r in runs if "headline_usage" in r]
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


def _format_table(with_runs: list[dict], without_runs: list[dict]) -> str:
    keys = [
        ("total_tokens", "total"),
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_creation_input_tokens", "cache_create"),
        ("cache_read_input_tokens", "cache_read"),
        ("duration_ms", "duration_ms"),
        ("num_turns", "num_turns"),
    ]
    lines = []
    lines.append(f"| metric | without_guidelines (n={len(without_runs)}) | with_guidelines (n={len(with_runs)}) | savings |")
    lines.append("| --- | --- | --- | --- |")
    for key, label in keys:
        wo = _summarize(without_runs, key)
        w = _summarize(with_runs, key)
        if not wo.get("n") or not w.get("n"):
            lines.append(f"| {label} | n/a | n/a | n/a |")
            continue
        wo_str = f"{wo['mean']:.0f} (range {wo['min']:.0f}–{wo['max']:.0f})"
        w_str = f"{w['mean']:.0f} (range {w['min']:.0f}–{w['max']:.0f})"
        delta = wo["mean"] - w["mean"]
        pct = (delta / wo["mean"] * 100.0) if wo["mean"] else 0.0
        lines.append(f"| {label} | {wo_str} | {w_str} | {delta:+.0f} ({pct:+.1f}%) |")
    return "\n".join(lines)


def _format_per_turn(run: dict) -> str:
    if not run.get("per_turn"):
        return "_(no transcript)_"
    rows = ["| # | role | input | output | cache_create | cache_read |", "| --- | --- | --- | --- | --- | --- |"]
    for i, turn in enumerate(run["per_turn"], 1):
        rows.append(
            f"| {i} | {turn.get('role','?')} | "
            f"{turn.get('input_tokens') or '-'} | "
            f"{turn.get('output_tokens') or '-'} | "
            f"{turn.get('cache_creation_input_tokens') or '-'} | "
            f"{turn.get('cache_read_input_tokens') or '-'} |"
        )
    return "\n".join(rows)


def _write_report(results_dir: Path, with_runs: list[dict], without_runs: list[dict], seeding_mode: str = "per-run") -> Path:
    report = []
    report.append("# Token-savings experiment\n")
    report.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    report.append("**Utterance 1 (seed):** " + UTTERANCE_LEARN)
    report.append("\n**Utterance 2 (measured):** " + UTTERANCE_MEASURE)
    report.append(f"\n**Seeding mode:** {seeding_mode}\n")
    report.append("## Summary\n")
    report.append(_format_table(with_runs, without_runs))
    report.append("")

    sample_with = next((r for r in with_runs if "headline_usage" in r), None)
    sample_without = next((r for r in without_runs if "headline_usage" in r), None)
    if sample_without:
        report.append("\n## Per-turn (representative without_guidelines run)\n")
        report.append(_format_per_turn(sample_without))
    if sample_with:
        report.append("\n## Per-turn (representative with_guidelines run)\n")
        report.append(_format_per_turn(sample_with))
        if sample_with.get("entities_seeded"):
            report.append("\n**Guidelines recallable in this run:** " + ", ".join(sample_with["entities_seeded"]))

    errors = [r for r in (with_runs + without_runs) if r.get("error")]
    if errors:
        report.append("\n## Errors\n")
        for r in errors:
            report.append(f"- {r['label']}: {r['error']}")

    path = results_dir / "report.md"
    path.write_text("\n".join(report) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3, help="runs per condition (default 3)")
    parser.add_argument(
        "--shared-seed",
        action="store_true",
        help="run utterance 1 once and reuse the seeded workspace for all with-guidelines measure runs (default: fresh seed per run)",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="don't delete the per-run workspaces (useful for debugging)",
    )
    args = parser.parse_args()

    _check_prerequisites()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_dir = REPO_ROOT / "experiments" / "results" / f"token_savings_{timestamp}"
    results_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = results_dir / "workspaces"
    workspace_root.mkdir(exist_ok=True)

    seeding_mode = "shared (one seed, N measure runs)" if args.shared_seed else "per-run (fresh seed for each measure run)"
    print(f"Results dir: {results_dir}")
    print(f"Runs per condition: {args.runs}")
    print(f"Seeding mode: {seeding_mode}")

    with_runs: list[dict] = []
    without_runs: list[dict] = []

    if args.shared_seed:
        print(f"\n=== with-guidelines (shared seed, {args.runs} measure runs) ===")
        with_runs.extend(_do_shared_seed_runs(workspace_root, args.runs))
        for i in range(1, args.runs + 1):
            print(f"\n=== without-guidelines run {i}/{args.runs} ===")
            without_runs.append(_do_without_guidelines_run(workspace_root, i))
    else:
        for i in range(1, args.runs + 1):
            print(f"\n=== with-guidelines run {i}/{args.runs} ===")
            with_runs.append(_do_with_guidelines_run(workspace_root, i))
            print(f"\n=== without-guidelines run {i}/{args.runs} ===")
            without_runs.append(_do_without_guidelines_run(workspace_root, i))

    raw_path = results_dir / "raw.json"
    raw_path.write_text(json.dumps(
        {"with_guidelines": with_runs, "without_guidelines": without_runs, "seeding_mode": seeding_mode},
        indent=2, default=str,
    ))
    report_path = _write_report(results_dir, with_runs, without_runs, seeding_mode=seeding_mode)

    print("\n" + "=" * 60)
    print(_format_table(with_runs, without_runs))
    print("=" * 60)
    print(f"\nReport: {report_path}")
    print(f"Raw:    {raw_path}")

    if not args.keep_workspaces:
        shutil.rmtree(workspace_root, ignore_errors=True)

    errors = [r for r in (with_runs + without_runs) if r.get("error")]
    if errors:
        print(f"\n{len(errors)} run(s) had errors — see report.md")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
