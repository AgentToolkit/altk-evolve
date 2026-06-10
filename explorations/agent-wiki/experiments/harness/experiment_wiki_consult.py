#!/usr/bin/env python3
# mypy: ignore-errors
# Exploration/reference code — not type-checked to the project standard.
"""A/B experiment: does pointing an agent at AGENTS.md alter its behavior?

REFERENCE ONLY — not runnable from this exploration tree as-is. The runner
needs sandbox assets that live in the full project, not under
explorations/agent-wiki/: a `claude-sandbox` Docker image, the demo workspace
(`demo/workspace`), the `_wiki_hint_plugin`, the Claude plugins dir, and the
`_format_samples` seeder. It is included to document *how* the wiki-helps
numbers in this directory's reports were produced (method, conditions,
scoring), not as a turnkey reproduction. The metric rollups under
`../metrics/` and the comparison scripts beside this file are the parts that
re-run standalone.

Paired design (utt1 → wiki → utt2):

- utt1 produces a small focal-length-extraction trajectory (reused from
  trajectory data; see Phase A in the plan file).
- wiki-example/ is a fresh single-trajectory wiki built from utt1's
  extracted guidelines. It contains AGENTS.md, _index.jsonl, 4 atomic
  guidelines, 1 summary.
- utt2 = "what lens model was used for @sample.jpg" — same image, related
  but different EXIF field. The wiki should help the agent bridge to
  LensModel (tag 0xA434) via the same Exif sub-IFD it documented for
  focal length.

For each condition (baseline, skill, prompt, claude_md), run N trials
in a fresh sandbox container and score three binary signals:

- read_agents_md: trajectory contains a Read of AGENTS.md
- cited_guideline: agent's final response mentions a guideline title or
  a key wiki concept (0xA434, 0x8769, ExifIFD)
- outcome_match: response contains "Google Pixel 4a Rear Wide Camera"

Usage (within the full project, not this exploration tree):
    uv run python explorations/agent-wiki/experiments/harness/experiment_wiki_consult.py \\
        --conditions baseline,skill,prompt,claude_md \\
        --trials 3
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

# Project root holds the sandbox assets this reference runner mounts
# (demo/workspace, platform-integrations/, tests/e2e/_wiki_hint_plugin). The
# script lives at <repo>/explorations/agent-wiki/experiments/harness/, so the
# repo root is parents[4]. (This runner is reference-only here — see the module
# docstring — and only resolves these assets when executed from the full repo.)
REPO_ROOT = Path(__file__).resolve().parents[4]
SANDBOX_IMAGE = "claude-sandbox"
TIMEOUT_SECONDS = 300
FORWARDED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_MODEL",
    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
    "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
)
WIKI_NAME = "wiki-example"  # default; overridable via --wiki on the CLI

# Test wiki must already exist; built by Phase A of the plan.
WIKI_SRC = REPO_ROOT / WIKI_NAME

# Plugins to mount.
PLUGINS_DIR = REPO_ROOT / "platform-integrations" / "claude" / "plugins"

# Demo workspace (sample.jpg only; no .evolve/entities/ confound).
DEMO_WORKSPACE = REPO_ROOT / "demo" / "workspace"


_STRONG_HINT = (
    f"Before doing anything else in this workspace, read `{WIKI_NAME}/AGENTS.md` "
    f"and follow its retrieval recipe to find guidelines that apply to your task. "
    f"Cite the guidelines you used."
)


_CODEBASE_SEED = {
    "src/__init__.py": "",
    "src/parser.py": (
        '"""Parser module for csv and json inputs."""\n'
        "\n"
        "def parse_csv(text: str) -> list:\n"
        "    # TODO: handle nested quotes properly\n"
        "    return [row.split(',') for row in text.splitlines()]\n"
        "\n"
        "def parse_json(text: str):\n"
        "    import json\n"
        "    return json.loads(text)\n"
    ),
    "src/writer.py": (
        "from .parser import parse_csv\n\ndef write_csv(rows):\n    return '\\n'.join(','.join(map(str, r)) for r in rows)\n"
    ),
    "src/api.py": (
        "from .parser import parse_csv, parse_json\n"
        "\n"
        "def fetch_and_parse(text: str, fmt: str):\n"
        "    return parse_csv(text) if fmt == 'csv' else parse_json(text)\n"
    ),
    "tests/__init__.py": "",
    "tests/test_parser.py": (
        "from src.parser import parse_csv\n\ndef test_parse_basic():\n    assert parse_csv('a,b\\nc,d') == [['a','b'], ['c','d']]\n"
    ),
    "README.md": ("# demo\n\nSmall Python project under `src/` with tests under `tests/`.\n"),
}


def _seed_codebase(ws: Path) -> None:
    for rel, content in _CODEBASE_SEED.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _seed_format_group(ws: Path, group: str) -> list[str]:
    """Seed image/archive/text format samples via the stdlib generators in
    `_format_samples.py`. Group is one of `image-formats`, `archive-formats`,
    `text-formats`.

    `_format_samples.py` is a project-level sandbox asset not shipped in this
    exploration tree (see the module docstring — this runner is reference-only).
    The import is deferred so the rest of the module imports cleanly; if a run
    actually reaches here without the asset, fail with a clear message.
    """
    try:
        from _format_samples import seed_into  # project sandbox asset, not in this tree
    except ImportError as exc:
        raise RuntimeError(
            "_seed_format_group requires _format_samples.py, a project-level sandbox "
            "asset not included in explorations/agent-wiki/. This A/B runner is "
            "reference-only here; run it from the full project."
        ) from exc

    return seed_into(ws, group)


def make_workspace(tmp_root: Path, condition: str, seed: str | None = None) -> Path:
    """Build a per-run workspace with the wiki + condition-specific setup +
    optional task-specific seed (e.g. a small mock python project)."""
    ws = tmp_root / "workspace"
    shutil.copytree(DEMO_WORKSPACE, ws, ignore=shutil.ignore_patterns(".evolve", "backup", "sandbox-backup"))
    # Mount the wiki inside the workspace at the same name the conditions reference.
    shutil.copytree(WIKI_SRC, ws / WIKI_NAME)
    # Per-condition setup
    if condition == "claude_md":
        (ws / "CLAUDE.md").write_text(
            f"Before non-trivial tasks in this repo, consult `{WIKI_NAME}/AGENTS.md` for relevant guidelines.\n",
            encoding="utf-8",
        )
    elif condition == "claude_md_strong":
        (ws / "CLAUDE.md").write_text(_STRONG_HINT + "\n", encoding="utf-8")
    # Per-task seed
    if seed == "codebase":
        _seed_codebase(ws)
    elif seed in ("image-formats", "archive-formats", "text-formats"):
        _seed_format_group(ws, seed)
    return ws


def build_prompt(condition: str, base_prompt: str) -> str:
    if condition == "skill":
        return "Use any skills that may help. " + base_prompt
    if condition == "prompt":
        return _STRONG_HINT + " " + base_prompt
    return base_prompt


_HINT_PLUGIN = REPO_ROOT / "tests" / "e2e" / "_wiki_hint_plugin"


def run_sandbox(workspace: Path, prompt: str, condition: str) -> dict:
    """Run a single sandbox session; return {stdout, stderr, returncode, duration_s}.

    Per condition extras:
    - `system_prompt`: pass `--append-system-prompt` with the strong hint.
    - `session_hook`:  mount _wiki_hint_plugin which fires a SessionStart
      hook printing the strong hint.

    Other conditions don't pass `--plugin-dir` (avoids the evolve-lite recall
    hook + recall skill confound). Trajectory comes from
    `--output-format stream-json` on stdout (one event per line).
    """
    cmd = ["docker", "run", "--rm"]
    for var in FORWARDED_ENV_VARS:
        if os.environ.get(var):
            cmd += ["-e", var]
    docker_args = ["-v", f"{workspace}:/workspace"]
    claude_extras = ""
    if condition == "session_hook":
        docker_args += ["-v", f"{_HINT_PLUGIN}:/plugins/_wiki_hint"]
        claude_extras = "--plugin-dir /plugins/_wiki_hint "
    if condition == "system_prompt":
        claude_extras = f"--append-system-prompt {json.dumps(_STRONG_HINT)} "
    cmd += docker_args
    cmd += [
        SANDBOX_IMAGE,
        "bash",
        "-c",
        f"claude {claude_extras}--dangerously-skip-permissions --output-format stream-json --verbose -p {json.dumps(prompt)}",
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    dt = time.time() - t0
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "duration_s": round(dt, 2),
    }


def parse_stream_json(stdout: str) -> tuple[list[str], str, list[dict]]:
    """Parse `claude -p --output-format stream-json --verbose` output.

    Returns (wiki_access_paths, assistant_text, all_events).

    `wiki_access_paths` collects any signal of wiki access — Read tool calls
    on wiki files, *or* Bash commands that cat/less/grep wiki files. The
    agent often reads wiki content via `cat <wiki-example>/AGENTS.md`
    rather than the Read tool, so we check both surfaces.
    """
    access_paths: list[str] = []
    chunks: list[str] = []
    events: list[dict] = []
    bash_pat = re.compile(
        r"\b(?:cat|less|head|tail|more|grep|sed)\b[^|;]*?(\S*?(?:AGENTS\.md|wiki-example/[A-Za-z0-9_./-]+))",
    )
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        if event.get("type") != "assistant":
            continue
        msg = event.get("message", {}) or {}
        content = msg.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    chunks.append(b.get("text", ""))
                elif b.get("type") == "tool_use":
                    name = b.get("name")
                    inp = b.get("input") or {}
                    if name == "Read":
                        fp = inp.get("file_path", "")
                        if fp:
                            access_paths.append(fp)
                    elif name == "Bash":
                        cmd = inp.get("command", "")
                        for m in bash_pat.finditer(cmd):
                            access_paths.append(m.group(1))
    return access_paths, "\n".join(chunks), events


def score(access_paths: list[str], assistant_text: str, task: dict) -> dict:
    text_lc = assistant_text.lower()
    # 1. read_agents_md — Read tool OR Bash cat/less/grep on AGENTS.md
    read_agents_md = any("AGENTS.md" in p for p in access_paths)
    # 2. cited_guideline: any expected filename mentioned in assistant text
    expected_files = task.get("expected_guideline_filenames") or []
    cited_filename = any(fn.lower() in text_lc for fn in expected_files)
    # OR any of the wiki concepts (the "match_any" set) appears
    match_any = task.get("outcome_match_any") or []
    cited_concept = any(s.lower() in text_lc for s in match_any)
    cited_guideline = cited_filename or cited_concept
    # 3. outcome_match: every required substring present
    must_all = task.get("outcome_match_all") or []
    outcome_match = all(s.lower() in text_lc for s in must_all)
    return {
        "read_agents_md": bool(read_agents_md),
        "cited_guideline": bool(cited_guideline),
        "outcome_match": bool(outcome_match),
    }


def main(argv: list[str] | None = None) -> int:
    # Declare upfront because --wiki may rebind these later in this function.
    global WIKI_NAME, WIKI_SRC, _STRONG_HINT
    parser = argparse.ArgumentParser()
    # `skill` condition is omitted: the agent-wiki/ family is not registered
    # as a plugin skill in evolve-lite's plugin.json (which only declares
    # ./skills/evolve-lite/). Loading the plugin to register it would also
    # pull in the recall hook + recall skill, which confound the test.
    parser.add_argument(
        "--conditions",
        default="baseline,prompt,claude_md",
        help="Comma-separated condition slugs. "
        "Available: baseline, prompt, claude_md, claude_md_strong, "
        "system_prompt, session_hook. (skill condition deferred — "
        "agent-wiki/* not registered as plugin skills.)",
    )
    parser.add_argument("--trials", type=int, default=3, help="Trials per condition")
    parser.add_argument("--task", default="t1-lens-model", help="Task id (or comma-separated task ids) from wiki_consult_tasks.yaml")
    parser.add_argument("--wiki", default=None, help=f"Wiki dir to mount at /workspace/<name>/. Default: {WIKI_NAME}")
    parser.add_argument("--out-root", default="experiments/results", help="Where to write the results dir")
    parser.add_argument("--keep-workspaces", action="store_true", help="Don't delete per-run workspaces (debug)")
    args = parser.parse_args(argv)

    # Allow --wiki to override the module-level constants. _STRONG_HINT is
    # already a module global that captures WIKI_NAME at import time, so
    # rebuild it whenever we override.
    if args.wiki:
        WIKI_NAME = args.wiki
        WIKI_SRC = REPO_ROOT / WIKI_NAME
        _STRONG_HINT = (
            f"Before doing anything else in this workspace, read `{WIKI_NAME}/AGENTS.md` "
            f"and follow its retrieval recipe to find guidelines that apply to your task. "
            f"Cite the guidelines you used."
        )

    if not WIKI_SRC.is_dir():
        print(f"error: {WIKI_SRC} does not exist. Run Phase A first.", file=sys.stderr)
        return 2

    # Load tasks (--task may be comma-separated). The task spec is checked in
    # beside this script under experiments/harness/.
    tasks_file = Path(__file__).resolve().parent / "wiki_consult_tasks.yaml"
    tasks = {t["id"]: t for t in yaml.safe_load(tasks_file.read_text())}
    task_ids = [t.strip() for t in args.task.split(",") if t.strip()]
    for tid in task_ids:
        if tid not in tasks:
            print(f"error: task {tid!r} not found in {tasks_file}", file=sys.stderr)
            return 2

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    valid = {"baseline", "skill", "prompt", "claude_md", "claude_md_strong", "system_prompt", "session_hook"}
    for c in conditions:
        if c not in valid:
            print(f"error: unknown condition {c!r}; valid: {sorted(valid)}", file=sys.stderr)
            return 2

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / args.out_root / f"wiki-consult-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = out_dir / "transcripts"

    runs_path = out_dir / "runs.jsonl"
    runs_f = runs_path.open("w", encoding="utf-8")

    print(f"writing results to {out_dir}", file=sys.stderr)
    print(f"conditions: {conditions}, trials: {args.trials}, tasks: {task_ids}", file=sys.stderr)

    summary: dict[tuple[str, str], list[dict]] = {(t, c): [] for t in task_ids for c in conditions}
    for tid in task_ids:
        task = tasks[tid]
        seed = task.get("seed")
        for condition in conditions:
            for trial in range(1, args.trials + 1):
                print(f"\n=== {tid} / {condition} / trial {trial}/{args.trials} ===", file=sys.stderr)
                tmp_root = out_dir / "_workspaces" / f"{tid}-{condition}-t{trial}"
                tmp_root.mkdir(parents=True, exist_ok=True)
                ws = make_workspace(tmp_root, condition, seed=seed)
                prompt = build_prompt(condition, task["prompt"])
                try:
                    run = run_sandbox(ws, prompt, condition)
                except subprocess.TimeoutExpired:
                    print(f"  ✗ TIMEOUT after {TIMEOUT_SECONDS}s — skipping this trial", file=sys.stderr)
                    runs_f.write(
                        json.dumps(
                            {
                                "task": tid,
                                "condition": condition,
                                "trial": trial,
                                "duration_s": TIMEOUT_SECONDS,
                                "returncode": None,
                                "read_agents_md": False,
                                "cited_guideline": False,
                                "outcome_match": False,
                                "access_paths_n": 0,
                                "assistant_text_len": 0,
                                "timed_out": True,
                            }
                        )
                        + "\n"
                    )
                    runs_f.flush()
                    if not args.keep_workspaces:
                        shutil.rmtree(tmp_root, ignore_errors=True)
                    continue
                access_paths, assistant_text, events = parse_stream_json(run["stdout"])
                sig = score(access_paths, assistant_text, task)
                row = {
                    "task": tid,
                    "condition": condition,
                    "trial": trial,
                    "duration_s": run["duration_s"],
                    "returncode": run["returncode"],
                    **sig,
                    "access_paths_n": len(access_paths),
                    "assistant_text_len": len(assistant_text),
                }
                runs_f.write(json.dumps(row) + "\n")
                runs_f.flush()
                summary[(tid, condition)].append(row)
                print(
                    f"  read_agents_md={sig['read_agents_md']}  "
                    f"cited_guideline={sig['cited_guideline']}  "
                    f"outcome_match={sig['outcome_match']}  "
                    f"({run['duration_s']:.0f}s)",
                    file=sys.stderr,
                )
                # Stash the stream-json output for spot-checks
                dst_dir2 = transcripts_dir / tid / condition
                dst_dir2.mkdir(parents=True, exist_ok=True)
                (dst_dir2 / f"trial-{trial}.jsonl").write_text(run["stdout"], encoding="utf-8")
                if run["returncode"] != 0:
                    (dst_dir2 / f"trial-{trial}.stderr.txt").write_text(run["stderr"], encoding="utf-8")
                if not args.keep_workspaces:
                    shutil.rmtree(tmp_root, ignore_errors=True)
    runs_f.close()

    # Render summary.md (one section per task)
    md_lines = [f"# Wiki-consult experiment — {ts}", ""]
    for tid in task_ids:
        task = tasks[tid]
        md_lines += [
            f"## Task `{tid}` — {task['prompt']!r}",
            "",
            f"Trials per condition: **{args.trials}**",
            "",
            "| Condition  | read AGENTS.md | cited guideline | outcome match | median runtime (s) |",
            "|------------|:--------------:|:---------------:|:-------------:|-------------------:|",
        ]
        for condition in conditions:
            rows = summary[(tid, condition)]
            n = len(rows)
            if n == 0:
                continue
            rd = sum(r["read_agents_md"] for r in rows)
            ct = sum(r["cited_guideline"] for r in rows)
            om = sum(r["outcome_match"] for r in rows)
            durs = sorted(r["duration_s"] for r in rows)
            median = durs[n // 2] if n % 2 == 1 else (durs[n // 2 - 1] + durs[n // 2]) / 2
            md_lines.append(f"| {condition:<10} | {rd}/{n} | {ct}/{n} | {om}/{n} | {median:.0f} |")
        md_lines.append("")
    md_lines.extend(
        [
            "",
            "Signals:",
            "",
            "- **read AGENTS.md**: agent's trajectory contains a `Read` of `AGENTS.md`.",
            "- **cited guideline**: agent's text contains an expected guideline filename or wiki concept (e.g. `0xA434`, `0x8769`, `ExifIFD`).",
            "- **outcome match**: agent's text contains all required substrings — for the lens-model task, the answer `Google Pixel 4a Rear Wide Camera`.",
            "",
            f"Runs JSONL: `{runs_path.relative_to(REPO_ROOT)}`",
            f"Transcripts: `{transcripts_dir.relative_to(REPO_ROOT)}/`",
        ]
    )
    (out_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"\nwrote {runs_path}", file=sys.stderr)
    print(f"wrote {out_dir / 'summary.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
