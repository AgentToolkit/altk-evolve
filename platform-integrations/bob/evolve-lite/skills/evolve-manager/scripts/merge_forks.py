#!/usr/bin/env python3
"""
evolve-manager merge-forks — versioning-aware fork merge pipeline

Merges .evolve/entities/ from one or more pre-cloned GitHub fork directories
into the local main-repo entity library with full regression protection.

Pipeline:
  0. [PR gate] Verify each fork has an open PR against the main repo (skips forks without one)
  1. Snapshot main-repo entity manifest (main_entity_slugs.json)
  2. Versioning-aware merge into .evolve/tmp/merge-workspace/entities/
       - same slug in both: Jaccard >= threshold → fork replaces main
       - same slug in both: Jaccard <  threshold → dual-section entity preserved
       - fork-only slugs: copied as-is, not subject to regression gate
  3. [A] Quality gate:   dedup.py --phase1-only on merged workspace
  4. [B] Pre-dedup test: rubric eval on main-repo entities → baseline_rate
  5. [C] Full dedup:     dedup.py (both phases) on merged workspace
  6. [D] Post-dedup test: rubric eval on main-repo entities → post_rate
  7. [E] Threshold gate: if post_rate < --threshold → exit 2 (user decides)
  8. On success: backup .evolve/entities/ → move merged workspace into place
  9. Write merge_report.json summarising the entire run

Exit codes:
  0 — merge succeeded, entities live in .evolve/entities/
  1 — hard failure (bad entities, script error)
  2 — threshold breach — main-repo rubric pass rate dropped; user must decide

Usage:
    python3 merge_forks.py --fork-dirs path/to/fork-a path/to/fork-b
    python3 merge_forks.py --fork-dirs path/to/fork-a --threshold 0.8
    python3 merge_forks.py --fork-dirs path/to/fork-a --dry-run
    python3 merge_forks.py --fork-dirs path/to/fork-a --version-diff-threshold 0.3
    python3 merge_forks.py --fork-dirs path/to/fork-a --main-repo owner/repo
    python3 merge_forks.py --fork-dirs path/to/fork-a --no-require-pr
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: locate lib/evolve-lite and sibling scripts
# ---------------------------------------------------------------------------
_script = Path(__file__).resolve()
_scripts_dir = _script.parent
_skills_root = _scripts_dir.parent.parent  # .bob/skills/

# Locate lib/evolve-lite by walking up
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    print("ERROR: Cannot find lib/evolve-lite/entity_io.py", file=sys.stderr)
    sys.exit(1)
sys.path.insert(0, str(_lib))

from entity_io import get_evolve_dir, markdown_to_entity  # noqa: E402

# Sibling tool paths
_dedup_script = _skills_root / "evolve-lite-dedup" / "scripts" / "dedup.py"
_gen_script = _skills_root / "evolve-lite-test" / "scripts" / "generate_pseudo_conversations.py"
_eval_script = _skills_root / "evolve-lite-test" / "scripts" / "run_skill_evaluation.py"

# ---------------------------------------------------------------------------
# Token-set Jaccard (mirrors refine.py — no import to avoid circular deps)
# ---------------------------------------------------------------------------
_STOP = {
    "the", "and", "for", "are", "but", "not", "with", "this", "that",
    "have", "from", "they", "will", "been", "when", "after", "before",
    "while", "how", "what", "just", "need", "want", "make", "sure",
    "some", "also", "about", "you", "your", "use", "run", "set",
}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def jaccard(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------

def entity_similarity_text(entity):
    return f"{entity.get('trigger', '')} {entity.get('content', '')}"


def read_entity(path):
    """Parse a .md entity file; returns dict with at least 'content'."""
    return markdown_to_entity(path)


def write_entity_file(path, frontmatter_dict, content):
    """Write a .md entity file with YAML frontmatter."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in frontmatter_dict.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(content)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_int(val, default=1):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Step 1 — Snapshot main-repo manifest
# ---------------------------------------------------------------------------

def snapshot_main_manifest(entities_dir, manifest_path):
    """
    Walk the live .evolve/entities/ directory and record every slug's:
      - origin: 'main'
      - base_version: int from frontmatter version field (default 1)
      - rubric: must_include terms derived from success_rubric (for regression gate)
      - rel_path: path relative to entities_dir (to find the file in merge workspace)
    """
    manifest = {}
    entities_dir = Path(entities_dir)
    for md_file in entities_dir.rglob("*.md"):
        slug = md_file.stem
        entity = read_entity(md_file)
        rubric = entity.get("success_rubric", "")
        rubric_terms = _rubric_to_must_include(rubric) if rubric else []
        manifest[slug] = {
            "origin": "main",
            "base_version": safe_int(entity.get("version", 1)),
            "fork_version": None,
            "dual_section": False,
            "rel_path": str(md_file.relative_to(entities_dir)),
            "rubric_terms": rubric_terms,
        }
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def _rubric_to_must_include(rubric_text):
    """Parse ## Success Rubric bullets into must_include terms (mirrors generate_pseudo_conversations.py)."""
    seen = set()
    terms = []
    for line in rubric_text.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        backtick_hits = re.findall(r"`([^`]+)`", line)
        candidates = [t.strip() for t in backtick_hits if t.strip()] if backtick_hits else [line]
        for t in candidates:
            if t not in seen:
                seen.add(t)
                terms.append(t)
    return terms


# ---------------------------------------------------------------------------
# Step 2 — Versioning-aware merge
# ---------------------------------------------------------------------------

def collect_fork_entities(fork_dirs):
    """
    Walk each fork dir for .evolve/entities/**/*.md files.
    Returns dict: slug -> (path, entity_dict) — last fork wins for fork-only
    slugs (earlier forks are already staged; later forks are added without
    overwriting earlier fork contributions unless the slug is the same).
    Fork entities keyed by slug; first occurrence wins (preserving earlier forks).
    """
    fork_entities = {}  # slug -> (path, entity, fork_name)
    for fork_dir in fork_dirs:
        fork_dir = Path(fork_dir)
        # Support both a bare fork dir and one containing .evolve/entities/ subtree
        entities_root = fork_dir / ".evolve" / "entities"
        if not entities_root.exists():
            entities_root = fork_dir  # caller may have already pointed at entities dir
        if not entities_root.exists():
            print(f"  WARNING: no entities found in {fork_dir}, skipping", file=sys.stderr)
            continue
        fork_name = fork_dir.name
        for md_file in entities_root.rglob("*.md"):
            slug = md_file.stem
            if slug not in fork_entities:  # first fork wins
                entity = read_entity(md_file)
                fork_entities[slug] = (md_file, entity, fork_name)
    return fork_entities


def merge_entities(main_entities_dir, fork_entities, workspace_entities_dir,
                   manifest, version_diff_threshold, dry_run):
    """
    Merge main-repo and fork entities into workspace_entities_dir.

    Strategy per slug:
      - main-only: copy verbatim, mark origin='main'
      - fork-only: copy verbatim, mark origin='fork'
      - both:
          Jaccard >= version_diff_threshold → fork replaces main (minor update)
          Jaccard <  version_diff_threshold → dual-section entity preserved

    Updates manifest in-place with fork provenance fields.
    Returns list of merge decisions for reporting.
    """
    main_entities_dir = Path(main_entities_dir)
    workspace_entities_dir = Path(workspace_entities_dir)
    workspace_entities_dir.mkdir(parents=True, exist_ok=True)

    decisions = []

    # --- main-only and overlap slugs ---
    for md_file in main_entities_dir.rglob("*.md"):
        slug = md_file.stem
        rel_path = md_file.relative_to(main_entities_dir)
        dest = workspace_entities_dir / rel_path
        main_entity = read_entity(md_file)

        if slug not in fork_entities:
            # main-only: copy verbatim
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(md_file, dest)
            decisions.append({"slug": slug, "action": "keep-main", "fork": None})
        else:
            fork_path, fork_entity, fork_name = fork_entities[slug]
            sim = jaccard(entity_similarity_text(main_entity),
                          entity_similarity_text(fork_entity))

            if sim >= version_diff_threshold:
                # Minor update — fork replaces main
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fork_path, dest)
                manifest[slug]["fork_version"] = safe_int(fork_entity.get("version"))
                manifest[slug]["origin"] = "merged"
                manifest[slug]["dual_section"] = False
                decisions.append({
                    "slug": slug, "action": "fork-replaces-main",
                    "fork": fork_name, "jaccard": round(sim, 3)
                })
            else:
                # Significant divergence — write dual-section entity
                fork_content = fork_entity.get("content", "")
                main_content = main_entity.get("content", "")
                merged_content = (
                    f"## Current Version\n\n{fork_content}\n\n"
                    f"## Previous Version\n\n{main_content}"
                )
                fork_ver = safe_int(fork_entity.get("version"))
                base_ver = safe_int(main_entity.get("version"))
                # Build merged frontmatter from main, overriding version fields
                fm = {k: v for k, v in main_entity.items()
                      if k not in ("content", "success_rubric", "changelog")}
                fm["version"] = fork_ver
                fm["base_version"] = base_ver
                if not dry_run:
                    write_entity_file(dest, fm, merged_content)
                manifest[slug]["fork_version"] = fork_ver
                manifest[slug]["origin"] = "merged"
                manifest[slug]["dual_section"] = True
                decisions.append({
                    "slug": slug, "action": "dual-section",
                    "fork": fork_name, "jaccard": round(sim, 3),
                    "base_version": base_ver, "fork_version": fork_ver,
                })

    # --- fork-only slugs ---
    for slug, (fork_path, fork_entity, fork_name) in fork_entities.items():
        if slug in manifest:
            continue  # already handled above
        # Determine relative path inside the fork's entities subtree
        entities_root = None
        for p in fork_path.parents:
            if p.name == "entities":
                entities_root = p
                break
        rel = fork_path.relative_to(entities_root) if entities_root else Path(fork_path.name)
        dest = workspace_entities_dir / rel
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fork_path, dest)
        manifest[slug] = {
            "origin": "fork",
            "base_version": None,
            "fork_version": safe_int(fork_entity.get("version")),
            "dual_section": False,
            "rel_path": str(rel),
            "rubric_terms": [],  # fork-only: no regression gate
        }
        decisions.append({"slug": slug, "action": "fork-only", "fork": fork_name})

    return decisions


# ---------------------------------------------------------------------------
# PR gate helpers
# ---------------------------------------------------------------------------

def _git_remote_url(repo_dir):
    """Return the 'origin' remote URL for a git repo directory, or None."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_dir), capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _parse_github_owner_repo(url):
    """
    Extract (owner, repo) from a GitHub remote URL.
    Supports any GitHub-flavoured host — github.com, github.ibm.com, GHE instances, etc.
    Handles:
      https://<host>/owner/repo[.git]
      git@<host>:owner/repo[.git]
    Returns (owner, repo) or (None, None).
    """
    if not url:
        return None, None
    # SSH form: git@<host>:owner/repo[.git]
    m = re.search(r"git@[^:]+:([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    # HTTPS form: https://<host>/owner/repo[.git]
    m = re.search(r"https?://[^/]+/([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _github_api(path, token=None, api_base=None):
    """Make a GET request to the GitHub API; returns parsed JSON or None on error."""
    base = (api_base or "https://github.ibm.com/api/v3").rstrip("/")
    url = f"{base}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  GitHub API error {e.code} for {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  GitHub API request failed: {e}", file=sys.stderr)
        return None


def _github_api_post(path, body, token=None, api_base=None):
    """Make a POST request to the GitHub API; returns parsed JSON or None on error."""
    base = (api_base or "https://github.ibm.com/api/v3").rstrip("/")
    url = f"{base}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"  GitHub API POST error {e.code} for {url}: {body_text[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  GitHub API POST request failed: {e}", file=sys.stderr)
        return None


def _github_api_patch(path, body, token=None, api_base=None):
    """Make a PATCH request to the GitHub API; returns parsed JSON or None on error."""
    base = (api_base or "https://github.ibm.com/api/v3").rstrip("/")
    url = f"{base}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"  GitHub API PATCH error {e.code} for {url}: {body_text[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  GitHub API PATCH request failed: {e}", file=sys.stderr)
        return None


def close_pr_with_comment(main_repo, pr_number, merge_summary, token=None, api_base=None):
    """
    Post a comment on a PR explaining that its entities were merged via evolve-manager,
    then close the PR.

    Args:
        main_repo:     'owner/repo' string for the upstream repository.
        pr_number:     int PR number.
        merge_summary: human-readable string summarising what was merged.
        token:         optional GitHub personal access token.
        api_base:      GitHub API base URL (default: https://github.ibm.com/api/v3).

    Returns:
        dict with keys: commented (bool), closed (bool), error (str|None)
    """
    result = {"commented": False, "closed": False, "error": None}

    comment_body = (
        "✅ **Entities merged via evolve-manager**\n\n"
        f"{merge_summary}\n\n"
        "The entity library from this fork was merged into the main repo using "
        "`evolve-manager merge-forks`. The PR is being closed as the changes have "
        "been incorporated directly into `.evolve/entities/`."
    )

    # Post comment
    comment_resp = _github_api_post(
        f"/repos/{main_repo}/issues/{pr_number}/comments",
        {"body": comment_body},
        token=token,
        api_base=api_base,
    )
    if comment_resp and comment_resp.get("id"):
        result["commented"] = True
    else:
        result["error"] = "Failed to post comment"

    # Close the PR
    close_resp = _github_api_patch(
        f"/repos/{main_repo}/pulls/{pr_number}",
        {"state": "closed"},
        token=token,
        api_base=api_base,
    )
    if close_resp and close_resp.get("state") == "closed":
        result["closed"] = True
    else:
        if result["error"]:
            result["error"] += "; Failed to close PR"
        else:
            result["error"] = "Failed to close PR"

    return result


def check_fork_has_open_pr(fork_dir, main_repo, token=None, api_base=None):
    """
    Check whether the given fork directory has an open PR against main_repo.

    Args:
        fork_dir: Path to the pre-cloned fork directory.
        main_repo: 'owner/repo' string for the upstream repository.
        token: Optional GitHub personal access token.
        api_base: GitHub API base URL (default: https://github.ibm.com/api/v3).

    Returns:
        dict with keys:
            has_pr (bool), pr_number (int|None), pr_title (str|None),
            fork_owner (str|None), pr_head (str|None), reason (str)
    """
    fork_dir = Path(fork_dir)
    remote_url = _git_remote_url(fork_dir)
    fork_owner, fork_repo = _parse_github_owner_repo(remote_url)

    if not fork_owner:
        return {
            "has_pr": False, "pr_number": None, "pr_title": None,
            "fork_owner": None, "pr_head": None,
            "reason": f"Could not determine fork owner from remote URL: {remote_url!r}",
        }

    # Query open PRs on the main repo
    # First try head filter; if API errors or returns empty, fetch all open PRs
    prs = _github_api(
        f"/repos/{main_repo}/pulls?state=open&head={fork_owner}:&per_page=100",
        token=token,
        api_base=api_base,
    )
    if not prs:
        # Fall back to fetching all open PRs without head filter
        prs = _github_api(
            f"/repos/{main_repo}/pulls?state=open&per_page=100",
            token=token,
            api_base=api_base,
        )

    if prs is None:
        return {
            "has_pr": False, "pr_number": None, "pr_title": None,
            "fork_owner": fork_owner, "pr_head": None,
            "reason": "GitHub API request failed (check network/token)",
        }

    # Filter client-side: check repo owner OR head label prefix OR PR author
    matching = []
    for pr in prs:
        head_repo_owner = (
            pr.get("head", {}).get("repo", {}) or {}
        ).get("owner", {}).get("login", "").lower()
        head_label = pr.get("head", {}).get("label", "").lower()
        pr_user = pr.get("user", {}).get("login", "").lower()
        owner_low = fork_owner.lower()

        if (
            head_repo_owner == owner_low
            or head_label.startswith(f"{owner_low}:")
            or pr_user == owner_low
        ):
            matching.append(pr)

    if matching:
        pr = matching[0]
        return {
            "has_pr": True,
            "pr_number": pr["number"],
            "pr_title": pr["title"],
            "fork_owner": fork_owner,
            "pr_head": pr["head"].get("label"),
            "reason": f"Open PR #{pr['number']}: {pr['title']!r}",
        }

    return {
        "has_pr": False, "pr_number": None, "pr_title": None,
        "fork_owner": fork_owner, "pr_head": None,
        "reason": f"No open PR found from {fork_owner} against {main_repo}",
    }


def _detect_main_repo(api_base=None, token=None):
    """
    Try to detect the main repo from the local git remote 'ce-artemis' or 'origin'.
    Returns 'owner/repo' string or None.
    """
    # Prefer the canonical upstream remote if present
    for remote_name in ("ce-artemis", "upstream", "origin"):
        url = None
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", remote_name],
                cwd=str(Path.cwd()), capture_output=True, text=True
            )
            if result.returncode == 0:
                url = result.stdout.strip()
        except Exception:
            pass
        if url:
            owner, repo = _parse_github_owner_repo(url)
            if owner and repo:
                return f"{owner}/{repo}"
    return None


# ---------------------------------------------------------------------------
# Merge report helpers
# ---------------------------------------------------------------------------

def write_merge_report(report_path, *, timestamp, fork_pr_results, accepted_forks,
                       skipped_forks, decisions, baseline_rate, post_rate,
                       threshold, outcome, outcome_reason, dry_run):
    """Write a structured merge_report.json summarising the entire run."""
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    action_counts = {}
    for d in (decisions or []):
        action_counts[d["action"]] = action_counts.get(d["action"], 0) + 1

    report = {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "outcome": outcome,
        "outcome_reason": outcome_reason,
        "threshold": threshold,
        "baseline_pass_rate": baseline_rate,
        "post_dedup_pass_rate": post_rate,
        "forks": {
            "accepted": accepted_forks,
            "skipped": skipped_forks,
            "pr_check_details": fork_pr_results,
        },
        "merge_decisions": {
            "summary": action_counts,
            "details": decisions or [],
        },
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report_path


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run_script(cmd, label):
    """Run a command, stream output, return exit code."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    result = subprocess.run(cmd)
    return result.returncode


def run_tests(entities_dir, manifest_path, output_dir, report_path, label,
              pinned_rubrics_path=None):
    """
    Generate pseudo-conversations filtered to main-repo slugs, then run evaluation.

    If pinned_rubrics_path is provided, must_include terms are taken from the
    original main-repo manifest rather than re-derived from the merged entity.
    This ensures forks cannot weaken rubrics and silently pass the regression gate.

    Returns the pass_rate float from the report JSON, or None on failure.
    """
    pseudo_dir = Path(output_dir) / "pseudo_conversations"
    results_dir = Path(output_dir) / "results"

    gen_cmd = [
        sys.executable, str(_gen_script),
        "--entities-dir", str(entities_dir),
        "--filter-slugs", str(manifest_path),
        "--output-dir", str(pseudo_dir),
    ]
    if pinned_rubrics_path:
        gen_cmd += ["--pinned-rubrics", str(pinned_rubrics_path)]
    rc = run_script(gen_cmd, f"{label} — generate fixtures")
    if rc != 0:
        print(f"ERROR: fixture generation failed (exit {rc})", file=sys.stderr)
        return None

    eval_cmd = [
        sys.executable, str(_eval_script),
        "--pseudo-conversations-dir", str(pseudo_dir),
        "--results-dir", str(results_dir),
        "--report", str(report_path),
    ]
    rc = run_script(eval_cmd, f"{label} — run evaluation")
    if rc not in (0, 1):  # exit 1 just means some tests failed — still get the rate
        print(f"ERROR: evaluation script crashed (exit {rc})", file=sys.stderr)
        return None

    if not Path(report_path).exists():
        print(f"ERROR: report not written to {report_path}", file=sys.stderr)
        return None

    with open(report_path, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    return report.get("pass_rate", 0.0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Merge fork entity libraries into main repo")
    parser.add_argument(
        "--fork-dirs", nargs="+", required=True,
        help="Pre-cloned fork directories to merge from"
    )
    parser.add_argument(
        "--threshold", type=float, default=1.0,
        help="Min rubric pass rate for main-repo tests (default: 1.0 = no regressions)"
    )
    parser.add_argument(
        "--version-diff-threshold", type=float, default=0.5,
        help="Jaccard similarity below which dual-section merging is used (default: 0.5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show all decisions without writing any entity files"
    )
    parser.add_argument(
        "--force-commit", action="store_true",
        help="Skip the threshold gate and commit the merge regardless of pass rate"
    )
    parser.add_argument(
        "--report-dir", default=None,
        help="Directory for dedup reports (default: .evolve/tests/dedup/)"
    )
    parser.add_argument(
        "--main-repo", default=None,
        help="Upstream GitHub repo as 'owner/repo' (auto-detected from git remote if omitted)"
    )
    parser.add_argument(
        "--require-pr", action="store_true", default=True,
        help="Skip forks that do not have an open PR against --main-repo (default: on)"
    )
    parser.add_argument(
        "--no-require-pr", dest="require_pr", action="store_false",
        help="Disable the PR gate — merge all provided fork dirs regardless of PR status"
    )
    parser.add_argument(
        "--github-token", default=None,
        help="GitHub personal access token for API calls (falls back to GITHUB_TOKEN env var)"
    )
    parser.add_argument(
        "--github-api-url", default=None,
        help="GitHub API base URL (default: https://github.ibm.com/api/v3)"
    )
    args = parser.parse_args()

    github_token = args.github_token or os.environ.get("GITHUB_TOKEN")
    github_api_url = args.github_api_url or os.environ.get("GITHUB_API_URL", "https://github.ibm.com/api/v3")

    evolve_dir = get_evolve_dir()
    live_entities = evolve_dir / "entities"
    tmp_dir = evolve_dir / "tmp"
    workspace_dir = tmp_dir / "merge-workspace"
    workspace_entities = workspace_dir / "entities"
    manifest_path = workspace_dir / "main_entity_slugs.json"
    backup_dir = tmp_dir / "pre-merge-backup"
    report_dir = Path(args.report_dir) if args.report_dir else evolve_dir / "tests" / "dedup"
    eval_dir = evolve_dir / "tests" / "evaluation"
    pre_report = eval_dir / "report.json"
    post_report = eval_dir / "report_post.json"
    merge_report_path = report_dir / "merge_report.json"

    run_timestamp = datetime.now(timezone.utc).isoformat()

    print(f"\nevolve-manager: merge-forks")
    print(f"  fork dirs         : {args.fork_dirs}")
    print(f"  threshold         : {args.threshold}")
    print(f"  version-diff-thr  : {args.version_diff_threshold}")
    print(f"  dry-run           : {args.dry_run}")
    print(f"  force-commit      : {args.force_commit}")
    print(f"  require-pr        : {args.require_pr}")
    print(f"  github-api-url    : {github_api_url}")
    print()

    if not live_entities.exists():
        print(f"ERROR: main entities directory not found: {live_entities}", file=sys.stderr)
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=[], accepted_forks=[],
            skipped_forks=[], decisions=[], baseline_rate=None, post_rate=None,
            threshold=args.threshold, outcome="error",
            outcome_reason=f"Main entities directory not found: {live_entities}",
            dry_run=args.dry_run,
        )
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Step 0 — PR gate
    # -----------------------------------------------------------------------
    fork_pr_results = []
    accepted_fork_dirs = []
    skipped_forks = []
    main_repo = None  # resolved below; kept in scope for PR-close step

    if args.require_pr:
        main_repo = args.main_repo or _detect_main_repo(api_base=github_api_url, token=github_token)
        if not main_repo:
            print(
                "ERROR: --require-pr is enabled but could not determine --main-repo.\n"
                "  Either pass --main-repo owner/repo or run from inside the git repo.",
                file=sys.stderr,
            )
            write_merge_report(
                merge_report_path,
                timestamp=run_timestamp, fork_pr_results=[], accepted_forks=[],
                skipped_forks=list(args.fork_dirs), decisions=[], baseline_rate=None,
                post_rate=None, threshold=args.threshold, outcome="error",
                outcome_reason="Could not determine main repo for PR gate",
                dry_run=args.dry_run,
            )
            sys.exit(1)

        print(f"\n{'='*70}")
        print(f"  STEP 0 — PR gate (main repo: {main_repo})")
        print(f"{'='*70}")

        for fork_dir in args.fork_dirs:
            result = check_fork_has_open_pr(fork_dir, main_repo, token=github_token, api_base=github_api_url)
            result["fork_dir"] = str(fork_dir)
            fork_pr_results.append(result)
            if result["has_pr"]:
                print(f"  ACCEPTED  {fork_dir}  — {result['reason']}")
                accepted_fork_dirs.append(fork_dir)
            else:
                print(f"  SKIPPED   {fork_dir}  — {result['reason']}")
                skipped_forks.append(str(fork_dir))

        if not accepted_fork_dirs:
            print("\n  No forks have an open PR against the main repo. Nothing to merge.")
            write_merge_report(
                merge_report_path,
                timestamp=run_timestamp, fork_pr_results=fork_pr_results,
                accepted_forks=[], skipped_forks=skipped_forks,
                decisions=[], baseline_rate=None, post_rate=None,
                threshold=args.threshold, outcome="skipped",
                outcome_reason="All forks skipped: no open PRs found",
                dry_run=args.dry_run,
            )
            print(f"\n  Merge report: {merge_report_path}")
            sys.exit(0)
    else:
        # PR gate disabled — accept all
        accepted_fork_dirs = list(args.fork_dirs)
        for fd in args.fork_dirs:
            fork_pr_results.append({
                "fork_dir": str(fd), "has_pr": None,
                "pr_number": None, "pr_title": None,
                "fork_owner": None, "pr_head": None,
                "reason": "PR gate disabled (--no-require-pr)",
            })

    # -----------------------------------------------------------------------
    # Clean workspace from previous runs
    # -----------------------------------------------------------------------
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Step 1 — Snapshot main-repo manifest
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP 1 — Snapshot main-repo entity manifest")
    print(f"{'='*70}")
    manifest = snapshot_main_manifest(live_entities, manifest_path)
    print(f"  Snapshotted {len(manifest)} main-repo entity slug(s) → {manifest_path}")

    # -----------------------------------------------------------------------
    # Step 2 — Versioning-aware merge
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP 2 — Versioning-aware merge")
    print(f"{'='*70}")
    fork_entities = collect_fork_entities(accepted_fork_dirs)
    print(f"  Discovered {len(fork_entities)} entity slug(s) across {len(accepted_fork_dirs)} fork(s)")

    decisions = merge_entities(
        main_entities_dir=live_entities,
        fork_entities=fork_entities,
        workspace_entities_dir=workspace_entities,
        manifest=manifest,
        version_diff_threshold=args.version_diff_threshold,
        dry_run=args.dry_run,
    )

    # Save updated manifest (now includes fork provenance)
    if not args.dry_run:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)

    # Print merge summary
    action_counts = {}
    for d in decisions:
        action_counts[d["action"]] = action_counts.get(d["action"], 0) + 1
    for action, count in sorted(action_counts.items()):
        print(f"  {action:<24} : {count}")
    dual_slugs = [d["slug"] for d in decisions if d.get("action") == "dual-section"]
    if dual_slugs:
        print(f"\n  Dual-section entities (significant divergence):")
        for s in dual_slugs:
            d = next(x for x in decisions if x["slug"] == s and x["action"] == "dual-section")
            print(f"    {s}  (base v{d['base_version']} → fork v{d['fork_version']}, jaccard={d['jaccard']})")

    if args.dry_run:
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=None, post_rate=None,
            threshold=args.threshold, outcome="dry-run",
            outcome_reason="Dry run — no files written",
            dry_run=True,
        )
        print(f"\n  Merge report: {merge_report_path}")
        print("\n[dry-run] No files written. Exiting.")
        sys.exit(0)

    # Build main-only slug filter (for test steps)
    main_slugs = {k: v for k, v in manifest.items() if v["origin"] in ("main", "merged")}
    main_filter_path = workspace_dir / "main_entity_slugs_filter.json"
    with open(main_filter_path, "w", encoding="utf-8") as fh:
        json.dump(main_slugs, fh, indent=2)

    # -----------------------------------------------------------------------
    # Step A — Quality gate on merged test fixtures
    # -----------------------------------------------------------------------
    dedup_p1_args = [
        sys.executable, str(_dedup_script),
        "--phase1-only",
        "--entities-dir", str(workspace_entities),
        "--report-dir", str(report_dir),
        "--manifest-dir", str(workspace_entities),  # build recall index from workspace, not live entities
    ]
    rc = run_script(dedup_p1_args, "STEP A — Quality gate (phase 1 only)")
    if rc != 0:
        print(f"\nERROR: Quality gate failed. Resolve entity issues before merging.")
        print(f"  Report: {report_dir / 'quality_gate_report.json'}")
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=None, post_rate=None,
            threshold=args.threshold, outcome="error",
            outcome_reason="Quality gate (phase 1) failed — resolve entity issues",
            dry_run=args.dry_run,
        )
        sys.exit(1)
    print("\n  Quality gate passed.")

    # -----------------------------------------------------------------------
    # Step B — Pre-dedup rubric test (main-repo entities only)
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP B — Pre-dedup rubric test (main-repo entities only)")
    print(f"{'='*70}")
    print(f"  Using pinned rubrics from original main-repo snapshot: {manifest_path}")
    baseline_rate = run_tests(
        entities_dir=workspace_entities,
        manifest_path=main_filter_path,
        output_dir=str(eval_dir / "pre_dedup"),
        report_path=str(pre_report),
        label="Pre-dedup",
        pinned_rubrics_path=manifest_path,
    )
    if baseline_rate is None:
        print("ERROR: Pre-dedup test run failed.", file=sys.stderr)
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=None, post_rate=None,
            threshold=args.threshold, outcome="error",
            outcome_reason="Pre-dedup rubric test run failed",
            dry_run=args.dry_run,
        )
        sys.exit(1)
    print(f"\n  Baseline pass rate (main-repo): {baseline_rate:.1%}")

    # -----------------------------------------------------------------------
    # Step C — Full skill dedup
    # -----------------------------------------------------------------------
    dedup_full_args = [
        sys.executable, str(_dedup_script),
        "--entities-dir", str(workspace_entities),
        "--report-dir", str(report_dir),
        "--manifest-dir", str(workspace_entities),  # build recall index from workspace, not live entities
    ]
    rc = run_script(dedup_full_args, "STEP C — Full skill dedup (both phases)")
    if rc != 0:
        print(f"\nERROR: Full dedup failed.")
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=baseline_rate, post_rate=None,
            threshold=args.threshold, outcome="error",
            outcome_reason="Full dedup (both phases) failed",
            dry_run=args.dry_run,
        )
        sys.exit(1)
    print("\n  Full dedup complete.")

    # -----------------------------------------------------------------------
    # Step D — Post-dedup rubric test (main-repo entities only)
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP D — Post-dedup rubric test (main-repo entities only)")
    print(f"{'='*70}")
    print(f"  Using pinned rubrics from original main-repo snapshot: {manifest_path}")
    post_rate = run_tests(
        entities_dir=workspace_entities,
        manifest_path=main_filter_path,
        output_dir=str(eval_dir / "post_dedup"),
        report_path=str(post_report),
        label="Post-dedup",
        pinned_rubrics_path=manifest_path,
    )
    if post_rate is None:
        print("ERROR: Post-dedup test run failed.", file=sys.stderr)
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=baseline_rate, post_rate=None,
            threshold=args.threshold, outcome="error",
            outcome_reason="Post-dedup rubric test run failed",
            dry_run=args.dry_run,
        )
        sys.exit(1)
    print(f"\n  Post-dedup pass rate (main-repo): {post_rate:.1%}")

    # -----------------------------------------------------------------------
    # Step E — Threshold gate
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP E — Threshold gate")
    print(f"{'='*70}")
    print(f"  Baseline : {baseline_rate:.1%}")
    print(f"  Post-dedup: {post_rate:.1%}")
    print(f"  Threshold : {args.threshold:.1%}")

    threshold_breached = post_rate < args.threshold and not args.force_commit

    if threshold_breached:
        print(f"\n  THRESHOLD BREACH: post-dedup pass rate {post_rate:.1%} < {args.threshold:.1%}")
        print()
        _print_regression_diff(report_dir, manifest)
        print()
        print("  Action required:")
        print("    --force-commit   to commit the merge anyway")
        print("    --dry-run        to inspect without writing")
        print("    Or roll back from: .evolve/tmp/pre-merge-backup/")
        write_merge_report(
            merge_report_path,
            timestamp=run_timestamp, fork_pr_results=fork_pr_results,
            accepted_forks=[str(d) for d in accepted_fork_dirs],
            skipped_forks=skipped_forks, decisions=decisions,
            baseline_rate=baseline_rate, post_rate=post_rate,
            threshold=args.threshold, outcome="threshold-breach",
            outcome_reason=f"Post-dedup pass rate {post_rate:.1%} < threshold {args.threshold:.1%}",
            dry_run=args.dry_run,
        )
        print(f"\n  Merge report: {merge_report_path}")
        sys.exit(2)

    # -----------------------------------------------------------------------
    # Commit: backup live entities, move workspace into place
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  COMMIT — Moving merged entities into .evolve/entities/")
    print(f"{'='*70}")

    # Backup
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(live_entities, backup_dir)
    print(f"  Backup written: {backup_dir}")

    # Replace live entities with merged workspace
    shutil.rmtree(live_entities)
    shutil.copytree(workspace_entities, live_entities)
    print(f"  Merged entities committed to: {live_entities}")

    total_merged = sum(1 for v in manifest.values() if v["origin"] == "merged")
    total_fork = sum(1 for v in manifest.values() if v["origin"] == "fork")
    total_main = sum(1 for v in manifest.values() if v["origin"] == "main")

    write_merge_report(
        merge_report_path,
        timestamp=run_timestamp, fork_pr_results=fork_pr_results,
        accepted_forks=[str(d) for d in accepted_fork_dirs],
        skipped_forks=skipped_forks, decisions=decisions,
        baseline_rate=baseline_rate, post_rate=post_rate,
        threshold=args.threshold, outcome="success",
        outcome_reason="Merged entities committed to .evolve/entities/",
        dry_run=args.dry_run,
    )

    # -----------------------------------------------------------------------
    # Close PRs for all accepted forks
    # -----------------------------------------------------------------------
    if args.require_pr and main_repo:
        print(f"\n{'='*70}")
        print("  CLOSING PRs — Notifying GitHub of completed merge")
        print(f"{'='*70}")

        action_summary_parts = []
        for action, count in sorted(action_counts.items()):
            action_summary_parts.append(f"- {count} entity/entities: `{action}`")
        action_summary = "\n".join(action_summary_parts) if action_summary_parts else "- entities merged"

        for pr_info in fork_pr_results:
            pr_number = pr_info.get("pr_number")
            fork_dir_str = pr_info.get("fork_dir", "unknown fork")
            if not pr_number:
                continue  # PR gate was disabled or fork was skipped

            merge_summary = (
                f"**Fork:** `{fork_dir_str}`\n"
                f"**Merge actions:**\n{action_summary}\n"
                f"**Baseline pass rate:** {baseline_rate:.1%}\n"
                f"**Post-dedup pass rate:** {post_rate:.1%}"
            )

            pr_result = close_pr_with_comment(
                main_repo=main_repo,
                pr_number=pr_number,
                merge_summary=merge_summary,
                token=github_token,
                api_base=github_api_url,
            )

            # Derive the web URL from the API base for the fallback close message
            _web_host = github_api_url.replace("https://", "").replace("/api/v3", "").rstrip("/")
            if pr_result["commented"] and pr_result["closed"]:
                print(f"  ✅  PR #{pr_number} commented and closed ({fork_dir_str})")
            elif pr_result["commented"]:
                print(f"  ⚠️   PR #{pr_number} commented but not closed: {pr_result['error']} ({fork_dir_str})")
            elif pr_result["closed"]:
                print(f"  ⚠️   PR #{pr_number} closed but comment failed: {pr_result['error']} ({fork_dir_str})")
            else:
                print(f"  ❌  PR #{pr_number} — could not comment or close: {pr_result['error']} ({fork_dir_str})")
                print(f"      Close it manually: https://{_web_host}/{main_repo}/pull/{pr_number}")
    else:
        print("\n  PR gate was disabled (--no-require-pr) — skipping PR close step.")

    # -----------------------------------------------------------------------
    # Step F — Regenerate unpinned fixtures to show what new/changed tests look like
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  STEP F — Generate unpinned fixtures for new tests (informational)")
    print(f"{'='*70}")
    live_pseudo = evolve_dir / "tests" / "pseudo_conversations"
    gen_new_cmd = [
        sys.executable, str(_gen_script),
        "--entities-dir", str(live_entities),
        "--output-dir", str(live_pseudo),
    ]
    rc = run_script(gen_new_cmd, "Generate new unpinned fixtures")
    if rc == 0:
        print(f"\n  New fixtures written to: {live_pseudo}")
        print(f"  Run /evolve-lite-run-tests to validate them.")
    else:
        print(f"\n  WARNING: Unpinned fixture generation failed (exit {rc}). Continuing.")

    print()
    print(f"  Summary:")
    print(f"    main-only entities : {total_main}")
    print(f"    fork-only entities : {total_fork}")
    print(f"    merged entities    : {total_merged}")
    print(f"    total              : {len(manifest)}")
    print(f"    baseline rate      : {baseline_rate:.1%}")
    print(f"    post-dedup rate    : {post_rate:.1%}")
    print()
    print(f"  Reports:")
    print(f"    merge report   : {merge_report_path}")
    print(f"    quality gate   : {report_dir / 'quality_gate_report.json'}")
    print(f"    refine         : {report_dir / 'refine_report.json'}")
    print(f"    pre-dedup eval : {pre_report}")
    print(f"    post-dedup eval: {post_report}")
    print()
    print(f"  New test fixtures: {live_pseudo}")
    print()
    print("Done.")
    sys.exit(0)


def _print_regression_diff(report_dir, manifest):
    """Print a human-readable diff of removed/merged entities from refine_report.json."""
    refine_report = Path(report_dir) / "refine_report.json"
    if not refine_report.exists():
        print("  (no refine report available)")
        return
    with open(refine_report, "r", encoding="utf-8") as fh:
        report = json.load(fh)
    clusters = report.get("clusters", [])
    if not clusters:
        print("  (no cluster decisions in refine report)")
        return
    print("  Dedup decisions affecting main-repo entities:")
    for cluster in clusters:
        decision = cluster.get("decision", "keep-all")
        if decision == "keep-all":
            continue
        members = cluster.get("members", [])
        for m in members:
            slug = Path(m.get("path", "")).stem
            origin = manifest.get(slug, {}).get("origin", "unknown")
            label = "[main]" if origin in ("main", "merged") else "[fork]"
            status = "KEPT" if m.get("kept") else "REMOVED"
            print(f"    {label} {slug:<50} {decision:<8} {status}")


if __name__ == "__main__":
    main()

# Made with Bob
