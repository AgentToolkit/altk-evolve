#!/usr/bin/env python3
"""
Phase 2 – Refine (Deduplication)

Groups entities by semantic similarity using token-set Jaccard distance.
For each cluster of similar skills, decides whether to:
  - keep-all  : skills are different enough (Jaccard < LOW threshold)
  - merge     : combine into a single consolidated entity (default for medium similarity)
  - discard   : remove the weaker duplicate (default for high similarity / near-identical)

By default runs non-interactively using automatic thresholds.
Use --interactive to review and decide each cluster manually.

Must only be run after quality_gate.py exits 0.

Usage:
    python3 refine.py
    python3 refine.py --interactive
    python3 refine.py --dry-run           # show decisions, make no changes
    python3 refine.py --threshold 0.5     # override merge threshold (default 0.45)
    python3 refine.py --report <path>
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: locate lib/evolve-lite
# ---------------------------------------------------------------------------
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find lib/evolve-lite above {_script}")
sys.path.insert(0, str(_lib))

from entity_io import (  # noqa: E402
    check_banality,
    get_evolve_dir,
    markdown_to_entity,
    entity_to_markdown,
    slugify,
    unique_filename,
)


# ===========================================================================
# Similarity
# ===========================================================================

_STOP = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "must", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "when", "after", "before", "while", "if", "how", "what", "my", "your",
    "just", "need", "want", "make", "sure", "some", "also", "about",
}


def _tokens(text):
    import re
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def jaccard(a, b):
    """Token-set Jaccard similarity between two strings. Returns float [0, 1]."""
    ta = _tokens(a)
    tb = _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union


def similarity_text(entity):
    """Combined text used for similarity comparison: trigger + content."""
    return f"{entity.get('trigger', '')} {entity.get('content', '')}"


# ===========================================================================
# Clustering (greedy single-linkage)
# ===========================================================================

def build_clusters(entities_with_paths, threshold):
    """
    Group entities into clusters where at least one pair has Jaccard >= threshold.

    Args:
        entities_with_paths: list of (path, entity_dict)
        threshold: float — Jaccard score at or above which two skills are "similar"

    Returns:
        List of clusters, each cluster is a list of (path, entity_dict, sim_to_prev).
        Singletons (no similar partner) are returned as clusters of size 1.
    """
    n = len(entities_with_paths)
    # Build similarity matrix
    sims = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = jaccard(
                similarity_text(entities_with_paths[i][1]),
                similarity_text(entities_with_paths[j][1]),
            )
            sims[i][j] = sims[j][i] = s

    # Greedy single-linkage clustering
    assigned = [False] * n
    clusters = []
    for i in range(n):
        if assigned[i]:
            continue
        cluster = [(i, 0.0)]
        assigned[i] = True
        for j in range(i + 1, n):
            if assigned[j]:
                continue
            # Check if j is similar to any member already in the cluster
            for ci, _ in cluster:
                if sims[ci][j] >= threshold:
                    cluster.append((j, sims[i][j]))
                    assigned[j] = True
                    break
        clusters.append(cluster)

    return [
        [(entities_with_paths[idx][0], entities_with_paths[idx][1], sim)
         for idx, sim in cluster]
        for cluster in clusters
    ]


# ===========================================================================
# Decision logic
# ===========================================================================

# Jaccard thresholds for automatic decisions
# >= DISCARD_THRESHOLD  → near-identical, discard the shorter one
# >= merge_threshold    → similar enough to merge
# <  merge_threshold    → keep-all (dissimilar)

DISCARD_THRESHOLD = 0.75


def _auto_decision(cluster, merge_threshold):
    """
    For a cluster of (path, entity, sim) tuples, return:
        action  – "keep-all" | "merge" | "discard"
        keep    – list of paths to keep
        remove  – list of paths to remove or merge away
        reason  – human-readable string
    """
    if len(cluster) == 1:
        return {"action": "keep-all", "keep": [cluster[0][0]], "remove": [], "reason": "singleton"}

    # Find max pairwise similarity
    paths = [c[0] for c in cluster]
    entities = [c[1] for c in cluster]
    max_sim = 0.0
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            s = jaccard(similarity_text(entities[i]), similarity_text(entities[j]))
            if s > max_sim:
                max_sim = s

    if max_sim >= DISCARD_THRESHOLD:
        # Keep the one with the most content (longest), discard others
        best = max(cluster, key=lambda c: len(c[1].get("content", "")))
        keep = [best[0]]
        remove = [c[0] for c in cluster if c[0] != best[0]]
        return {
            "action": "discard",
            "keep": keep,
            "remove": remove,
            "max_similarity": round(max_sim, 3),
            "reason": f"near-identical (Jaccard={max_sim:.2f} ≥ {DISCARD_THRESHOLD}) — keeping the most detailed version",
        }
    else:
        # Merge: the primary skill gets enriched trigger; others removed
        primary = max(cluster, key=lambda c: len(c[1].get("content", "")))
        keep = [primary[0]]
        remove = [c[0] for c in cluster if c[0] != primary[0]]
        return {
            "action": "merge",
            "keep": keep,
            "remove": remove,
            "merge_into": primary[0],
            "max_similarity": round(max_sim, 3),
            "reason": f"similar (Jaccard={max_sim:.2f} ≥ {merge_threshold}) — merging into richest skill",
        }


def _interactive_decision(cluster, merge_threshold):
    """Print cluster details and ask the user to choose an action."""
    print()
    print("─" * 70)
    print(f"  CLUSTER of {len(cluster)} similar skills")
    print()
    for i, (path, entity, sim) in enumerate(cluster):
        label = f"  [{i+1}] {Path(path).stem}"
        sim_str = f"  sim={sim:.2f}" if i > 0 else "  (primary)"
        print(f"{label}{sim_str}")
        print(f"      trigger : {entity.get('trigger', '')[:70]}")
        print(f"      content : {entity.get('content', '')[:100].strip()}")
        print()

    auto = _auto_decision(cluster, merge_threshold)
    print(f"  Suggested: {auto['action'].upper()}  — {auto['reason']}")
    print()
    print("  Options:")
    print("    k  keep-all  — no changes, all skills stay")
    print("    m  merge     — merge all into richest skill")
    print("    d  discard   — keep richest, remove others")
    print("    s  skip      — skip this cluster (decide later)")

    while True:
        choice = input("  Choice [k/m/d/s] (Enter = accept suggestion): ").strip().lower()
        if choice == "" or choice == auto["action"][0]:
            return auto
        if choice == "k":
            paths = [c[0] for c in cluster]
            return {"action": "keep-all", "keep": paths, "remove": [], "reason": "manual: keep-all"}
        if choice == "m":
            primary = max(cluster, key=lambda c: len(c[1].get("content", "")))
            keep = [primary[0]]
            remove = [c[0] for c in cluster if c[0] != primary[0]]
            return {"action": "merge", "keep": keep, "remove": remove,
                    "merge_into": primary[0], "reason": "manual: merge"}
        if choice == "d":
            primary = max(cluster, key=lambda c: len(c[1].get("content", "")))
            keep = [primary[0]]
            remove = [c[0] for c in cluster if c[0] != primary[0]]
            return {"action": "discard", "keep": keep, "remove": remove, "reason": "manual: discard"}
        if choice == "s":
            paths = [c[0] for c in cluster]
            return {"action": "keep-all", "keep": paths, "remove": [], "reason": "skipped by user"}
        print("  Please enter k, m, d, s, or press Enter.")


# ===========================================================================
# Apply decisions
# ===========================================================================

def _sentence_tokens(text):
    """Split content into a list of stripped, non-empty sentences/lines."""
    import re as _re
    # Split on sentence-ending punctuation or newlines; keep the delimiter
    raw = _re.split(r"(?<=[.!?])\s+|\n+", text)
    return [s.strip() for s in raw if s.strip()]


def _unique_sentences(primary_sentences, secondary_sentences, similarity_threshold=0.5):
    """
    Return sentences from *secondary* that are not already covered by *primary*.

    A secondary sentence is considered covered if its token-set Jaccard score
    against any primary sentence is >= *similarity_threshold*.
    """
    import re as _re

    def _tok(s):
        words = _re.findall(r"[a-z0-9]+", s.lower())
        return {w for w in words if len(w) > 2}

    def _jac(a, b):
        ta, tb = _tok(a), _tok(b)
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    result = []
    for sec in secondary_sentences:
        if not any(_jac(sec, pri) >= similarity_threshold for pri in primary_sentences):
            result.append(sec)
    return result


def _build_merged_entity(cluster):
    """
    Build a merged entity from a cluster.
    - trigger  : all unique triggers joined with '; '
    - content  : primary content + unique sentences from each secondary skill,
                 appended as a new paragraph so nothing is silently dropped
    - rationale: combined unique rationale lines (if present)
    - other frontmatter: taken from the richest (longest-content) skill
    """
    primary_path, primary_entity, _ = max(cluster, key=lambda c: len(c[1].get("content", "")))

    # --- merge triggers ---
    all_triggers = []
    seen_triggers: set = set()
    for _, ent, _ in cluster:
        t = ent.get("trigger", "").strip()
        if t and t not in seen_triggers:
            all_triggers.append(t)
            seen_triggers.add(t)

    # --- merge content ---
    primary_sentences = _sentence_tokens(primary_entity.get("content", ""))
    extra_sentences: list = []
    for _, ent, _ in cluster:
        if ent is primary_entity:
            continue
        sec_sentences = _sentence_tokens(ent.get("content", ""))
        extra_sentences.extend(_unique_sentences(primary_sentences + extra_sentences, sec_sentences))

    if extra_sentences:
        merged_content = (
            primary_entity.get("content", "").rstrip()
            + "\n\n"
            + " ".join(extra_sentences)
        )
    else:
        merged_content = primary_entity.get("content", "")

    # --- merge rationale ---
    primary_rationale = primary_entity.get("rationale", "").strip()
    rationale_parts = [primary_rationale] if primary_rationale else []
    seen_rat: set = {primary_rationale} if primary_rationale else set()
    for _, ent, _ in cluster:
        if ent is primary_entity:
            continue
        r = ent.get("rationale", "").strip()
        if r and r not in seen_rat:
            rationale_parts.append(r)
            seen_rat.add(r)

    merged = dict(primary_entity)
    merged["trigger"] = "; ".join(all_triggers)
    merged["content"] = merged_content
    if rationale_parts:
        merged["rationale"] = " | ".join(rationale_parts)
    return merged, primary_path


def apply_decision(decision, cluster, entities_dir, dry_run):
    """
    Execute the decision. Returns a summary dict.
    """
    action = decision["action"]
    removed_paths = []
    merged_path = None

    if action == "keep-all":
        return {"action": action, "removed": [], "merged_path": None,
                "reason": decision.get("reason", "")}

    if action == "discard":
        for path in decision["remove"]:
            if not dry_run:
                try:
                    Path(path).unlink()
                    removed_paths.append(path)
                except OSError as e:
                    print(f"  ⚠  Could not remove {path}: {e}", file=sys.stderr)
            else:
                removed_paths.append(path)

    elif action == "merge":
        merged_entity, primary_path = _build_merged_entity(cluster)
        merged_md = entity_to_markdown(merged_entity)

        if not dry_run:
            # Write merged content back to the primary file
            fd, tmp = tempfile.mkstemp(dir=Path(primary_path).parent, suffix=".tmp")
            try:
                os.write(fd, merged_md.encode("utf-8"))
                os.close(fd)
                fd = None
                os.replace(tmp, primary_path)
            except BaseException:
                if fd is not None:
                    os.close(fd)
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            merged_path = str(primary_path)

            # Remove the non-primary duplicates
            for path in decision["remove"]:
                try:
                    Path(path).unlink()
                    removed_paths.append(path)
                except OSError as e:
                    print(f"  ⚠  Could not remove {path}: {e}", file=sys.stderr)
        else:
            merged_path = str(primary_path)
            removed_paths = list(decision["remove"])

    return {
        "action": action,
        "removed": removed_paths,
        "merged_path": merged_path,
        "reason": decision.get("reason", ""),
    }


# ===========================================================================
# Main
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2: find and resolve similar/duplicate skills"
    )
    parser.add_argument("--entities-dir", default=None)
    parser.add_argument("--threshold", type=float, default=0.45,
                        help="Jaccard similarity threshold for grouping (default: 0.45)")
    parser.add_argument("--interactive", action="store_true",
                        help="Review each cluster manually before acting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show decisions without making changes")
    parser.add_argument("--no-prune", action="store_true",
                        help="Skip banality pruning (keep all entities regardless of quality)")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()
    entities_dir = Path(args.entities_dir) if args.entities_dir \
        else evolve_dir / "entities"

    if not entities_dir.exists():
        print(f"No entities directory at {entities_dir}.")
        sys.exit(0)

    _OWNED_TYPE_DIRS = {"atomic-skill", "guideline", "skill-flow"}
    md_files = sorted(
        p for p in entities_dir.glob("**/*.md")
        if not p.is_symlink()
        and ".git" not in p.parts
        and "subscribed" not in p.relative_to(entities_dir).parts
        and any(part in _OWNED_TYPE_DIRS for part in p.relative_to(entities_dir).parts)
    )
    if not md_files:
        print("No entity files found.")
        sys.exit(0)

    # Load all entities, grouped by entity type so dedup only happens within
    # atomic-skill, guideline, and skill-flow buckets.
    entities_by_type = {"atomic-skill": [], "guideline": [], "skill-flow": []}
    for path in md_files:
        try:
            entity = markdown_to_entity(path)
            if entity.get("content") and entity.get("type") in entities_by_type:
                entities_by_type[entity["type"]].append((path, entity))
        except Exception as exc:
            print(f"  ⚠  Skipping unparseable file {path}: {exc}", file=sys.stderr)

    loaded_count = sum(len(items) for items in entities_by_type.values())
    print(f"Loaded {loaded_count} entities")
    print(f"Similarity threshold: {args.threshold}")
    if args.dry_run:
        print("DRY RUN — no changes will be made")
    print()

    # ── Banality prune (pre-clustering) ──────────────────────────────────────
    # Remove entities that are too generic to provide recall value before
    # the similarity clustering runs.  Skill-flows are exempt — they encode
    # composition order which is non-obvious even when their steps are simple.
    prune_summaries = []
    if not args.no_prune:
        print("PRUNE — banality check")
        print("=" * 70)
        for entity_type in ("atomic-skill", "guideline"):
            keep = []
            for path, entity in entities_by_type[entity_type]:
                is_banal, reason = check_banality(entity)
                if is_banal:
                    slug = Path(path).stem
                    dry_tag = " [DRY RUN]" if args.dry_run else ""
                    print(f"  🗑  prune{dry_tag}  {slug}")
                    print(f"      reason: {reason}")
                    if not args.dry_run:
                        try:
                            Path(path).unlink()
                        except OSError as e:
                            print(f"  ⚠  Could not remove {path}: {e}", file=sys.stderr)
                            keep.append((path, entity))
                            continue
                    prune_summaries.append({
                        "path": str(path),
                        "slug": slug,
                        "reason": reason,
                        "dry_run": args.dry_run,
                    })
                else:
                    keep.append((path, entity))
            entities_by_type[entity_type] = keep

        if prune_summaries:
            dry_note = " (dry run)" if args.dry_run else ""
            print(f"\n  Pruned {len(prune_summaries)} banal entity(ies){dry_note}.")
        else:
            print("  No banal entities found.")
        print()

    # Build clusters within each entity type only
    clusters = []
    for entity_type in ("atomic-skill", "guideline", "skill-flow"):
        typed_entities = entities_by_type[entity_type]
        if typed_entities:
            clusters.extend(build_clusters(typed_entities, args.threshold))
    multi_clusters = [c for c in clusters if len(c) > 1]
    singleton_count = sum(1 for c in clusters if len(c) == 1)

    print(f"Clusters: {len(clusters)} total  ({len(multi_clusters)} with duplicates, {singleton_count} singletons)")

    if not multi_clusters:
        msg = "✅ No similar entities found within the same type — library is already clean."
        if prune_summaries:
            msg += f" ({len(prune_summaries)} banal entities pruned above.)"
        print(f"\n{msg}")

    print()
    print("REFINE — Phase 2")
    print("=" * 70)

    summaries = []
    total_removed = 0
    total_merged = 0

    for cluster in multi_clusters:
        if args.interactive:
            decision = _interactive_decision(cluster, args.threshold)
        else:
            decision = _auto_decision(cluster, args.threshold)

        result = apply_decision(decision, cluster, entities_dir, args.dry_run)
        summaries.append({
            "cluster": [str(c[0]) for c in cluster],
            "decision": decision,
            "result": result,
        })

        action = result["action"]
        dry_tag = " [DRY RUN]" if args.dry_run else ""
        if action == "keep-all":
            slugs = [Path(c[0]).stem for c in cluster]
            print(f"  ⟳  keep-all  {slugs}")
        elif action == "discard":
            removed_slugs = [Path(p).stem for p in result["removed"]]
            kept_slugs = [Path(c[0]).stem for c in cluster if str(c[0]) not in result["removed"]]
            print(f"  🗑  discard{dry_tag}  kept={kept_slugs}  removed={removed_slugs}")
            print(f"      reason: {result['reason']}")
            total_removed += len(result["removed"])
        elif action == "merge":
            slugs = [Path(c[0]).stem for c in cluster]
            print(f"  ⤵  merge{dry_tag}    {slugs} → {Path(result['merged_path']).stem}")
            print(f"      reason: {result['reason']}")
            total_merged += 1
            total_removed += len(result["removed"])

    print()
    print("REFINE SUMMARY")
    print("=" * 70)
    if prune_summaries:
        print(f"  Banal entities pruned : {len(prune_summaries)}")
    print(f"  Clusters resolved     : {len(multi_clusters)}")
    print(f"  Skills removed        : {total_removed}")
    print(f"  Skills merged         : {total_merged}")
    if args.dry_run:
        print("  (no changes written — dry run)")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "generated_at": datetime.now().isoformat(),
            "threshold": args.threshold,
            "dry_run": args.dry_run,
            "banal_pruned": len(prune_summaries),
            "prune_summaries": prune_summaries,
            "clusters_with_duplicates": len(multi_clusters),
            "total_removed": total_removed,
            "total_merged": total_merged,
            "summaries": summaries,
        }
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"\nReport written: {report_path}")

    sys.exit(0)


if __name__ == "__main__":
    main()

# Made with Bob
