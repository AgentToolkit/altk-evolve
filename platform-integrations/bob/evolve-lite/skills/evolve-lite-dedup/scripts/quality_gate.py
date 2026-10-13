#!/usr/bin/env python3
"""
Phase 1 – Quality Gate

Runs ALL three test suites against the entity library before dedup is allowed:

  1. FORMAT CHECK      – required frontmatter fields present; content non-empty;
                         type is a valid value; trigger is not suspiciously short.

  2. RECALL TEST       – the skill must rank in the top-3 of the manifest for the
                         user message derived from its own trigger (same heuristic
                         as run_recall_tests.py).

  3. SKILL EVALUATION  – the skill content must contain its own must_include terms
                         (self-consistency check from run_skill_evaluation.py).
                         For skill-flows, all referenced atomic_skills must exist.

Exits 0 only when every skill passes all three suites.
A non-zero exit blocks Phase 2 (refine.py / dedup.py).

Usage:
    python3 quality_gate.py
    python3 quality_gate.py --verbose
    python3 quality_gate.py --entities-dir <path>
    python3 quality_gate.py --report <path>
"""

import argparse
import json
import re
import sys
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
    get_evolve_dir,
    load_manifest,
    find_recall_entity_dirs,
    dedupe_manifest_entries,
    markdown_to_entity,
    slugify,
)

# Pull in pseudo-conversation helpers from the evolve-lite-test sibling
_test_scripts = _script.parents[2] / "evolve-lite-test" / "scripts"
if _test_scripts.is_dir():
    sys.path.insert(0, str(_test_scripts))
    try:
        from generate_pseudo_conversations import (  # noqa: E402
            build_pseudo_conversation,
            trigger_to_realistic_question,
            build_expected_behaviour,
        )
        _HAVE_BUILDER = True
    except ImportError:
        _HAVE_BUILDER = False
else:
    _HAVE_BUILDER = False


# ===========================================================================
# Suite 1 – Format check
# ===========================================================================

REQUIRED_FRONTMATTER = ("type", "trigger", "owner", "visibility")
VALID_TYPES = {"guideline", "atomic-skill", "skill-flow"}


def check_format(entity):
    """Return list of format violation strings, or [] if clean."""
    issues = []
    for field in REQUIRED_FRONTMATTER:
        if not entity.get(field):
            issues.append(f"missing frontmatter field: '{field}'")
    entity_type = entity.get("type", "")
    if entity_type and entity_type not in VALID_TYPES:
        issues.append(f"invalid type '{entity_type}' — must be one of {sorted(VALID_TYPES)}")
    if not entity.get("content", "").strip():
        issues.append("content body is empty")
    trigger = entity.get("trigger", "").strip()
    if trigger and len(trigger) < 10:
        issues.append(f"trigger too short ({len(trigger)} chars): '{trigger}'")
    if entity.get("type") == "skill-flow":
        if not entity.get("atomic_skills", "").strip():
            issues.append("skill-flow has no atomic_skills references")
    # version must be a positive integer when present
    version_raw = entity.get("version", "")
    if version_raw:
        try:
            v = int(version_raw)
            if v < 1:
                raise ValueError
        except (ValueError, TypeError):
            issues.append(f"version must be a positive integer, got: '{version_raw}'")
    return issues


def check_version(entity):
    """
    Suite 5 – Version / changelog consistency.

    Returns a list of *warning* strings (non-blocking — does not fail the gate).
    A warning fires when version > 1 but the ## Changelog section has fewer
    entries than the stated version number.

    Changelog entries are counted by lines that look like version markers:
    '- v<n>:', 'v<n>:', '## v<n>', or '**v<n>**'.  A bare list of bullet
    points (one per version bump) also counts — each '-' or '*' at the start
    of a line is treated as one entry.
    """
    warnings = []
    version_raw = entity.get("version", "")
    if not version_raw:
        return warnings

    try:
        version = int(version_raw)
    except (ValueError, TypeError):
        return warnings  # already caught by check_format

    if version <= 1:
        return warnings

    changelog = entity.get("changelog", "").strip()
    if not changelog:
        warnings.append(
            f"version={version} but ## Changelog section is missing — "
            "add a changelog entry for each version bump"
        )
        return warnings

    # Count lines that look like individual version entries
    entry_count = sum(
        1 for line in changelog.splitlines()
        if re.match(r"^\s*[-*]|^\s*v\d+\b|^\s*#+\s*v\d+\b|\*\*v\d+\b", line.strip())
    )
    if entry_count == 0:
        # Any non-empty paragraph counts as at least one entry
        entry_count = 1

    if entry_count < version:
        warnings.append(
            f"version={version} but only {entry_count} changelog "
            f"entry(ies) found — add entries for each version bump"
        )
    return warnings


# ===========================================================================
# Suite 4 – Filename & trigger naming quality
# ===========================================================================

# Verbs that indicate a well-formed imperative/conditional trigger
_TRIGGER_VERB_PATTERNS = re.compile(
    r"\b(when|after|before|while|if|create|set up|setup|install|configure|run|ensure|"
    r"import|activate|deploy|use|handle|build|check|validate|update|add|remove|generate|"
    r"upload|download|enable|disable|start|stop|connect|authenticate|define|write|"
    r"initialize|initialise|debug|troubleshoot|fix|resolve|migrate)\b",
    re.IGNORECASE,
)

# Patterns that indicate a stutter / accidental duplication in a trigger
_STUTTER_PATTERN = re.compile(
    r"\b(\w{4,})\b.{0,10}\b\1\b", re.IGNORECASE
)

# Minimum fraction of trigger tokens that must appear in the slug (case-insensitive)
_SLUG_COVERAGE_THRESHOLD = 0.25

_SLUG_STOP = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "must", "can", "this",
    "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "when", "after", "before", "while", "if", "how", "what", "my", "your",
    "just", "need", "want", "make", "sure", "some", "also", "about",
}


def _slug_tokens(text):
    """Split a slug or plain text into meaningful lowercase tokens."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _SLUG_STOP and len(w) > 2]


def check_naming(entity, path):
    """
    Suite 4: check that the filename slug and trigger text are well-formed.

    Returns a list of issue strings (empty = clean).
    Distinguishes warnings (prefixed 'warn:') from hard failures so callers
    can decide severity.  Currently all issues are treated equally (hard fail).
    """
    issues = []
    slug = Path(path).stem
    trigger = entity.get("trigger", "").strip()

    # --- Trigger checks ---
    if trigger:
        # Must contain at least one recognised action verb or 'when/after/...'
        if not _TRIGGER_VERB_PATTERNS.search(trigger):
            issues.append(
                "trigger has no recognisable action verb or conditional keyword"
            )

        # Stutter: same word repeated close together (e.g. "creating create")
        stutter = _STUTTER_PATTERN.search(trigger)
        if stutter:
            issues.append(
                f"trigger contains likely stutter/duplication: "
                f"'{stutter.group(0).strip()}'"
            )

        # Trigger should not be a verbatim copy of the content
        content = entity.get("content", "").strip()
        if content and trigger.lower() == content.lower():
            issues.append("trigger is identical to content body")

        # Minimum useful length (already covered by check_format for < 10,
        # add a stronger floor here)
        if len(trigger) < 15:
            issues.append(
                f"trigger is too brief ({len(trigger)} chars) — add more context"
            )

    # --- Slug / filename checks ---
    if slug:
        # Slug must not contain spaces or uppercase (would indicate wrong creation)
        if re.search(r"[A-Z ]", slug):
            issues.append(f"filename slug contains uppercase or spaces: '{slug}'")

        # Slug should have at least 2 meaningful tokens after stopword removal
        slug_tok = _slug_tokens(slug)
        if len(slug_tok) < 2:
            issues.append(
                f"filename slug is too generic (only {len(slug_tok)} meaningful "
                f"token(s)): '{slug}'"
            )

        # Slug should overlap meaningfully with the trigger
        if trigger:
            trig_tok = set(_slug_tokens(trigger))
            if trig_tok:
                overlap = len(set(slug_tok) & trig_tok) / len(trig_tok)
                if overlap < _SLUG_COVERAGE_THRESHOLD:
                    issues.append(
                        f"filename slug has low overlap with trigger "
                        f"({overlap:.0%} < {_SLUG_COVERAGE_THRESHOLD:.0%}): "
                        f"slug='{slug}'"
                    )

    return issues


# ===========================================================================
# Suite 2 – Recall test (keyword-score heuristic)
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


def _tokenise(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOP and len(w) > 2]


def _score_trigger(trigger, user_msg):
    t_tokens = set(_tokenise(trigger))
    u_tokens = set(_tokenise(user_msg))
    matched = t_tokens & u_tokens
    return sum(len(w) for w in matched), matched


def _rank_manifest(manifest, user_msg):
    scored = []
    for entry in manifest:
        s, matched = _score_trigger(entry["trigger"], user_msg)
        scored.append({**entry, "score": s, "matched_terms": list(matched)})
    return sorted(scored, key=lambda e: e["score"], reverse=True)


def _derive_user_message(entity):
    """Generate a realistic user message from the entity's trigger."""
    trigger = entity.get("trigger", "")
    if _HAVE_BUILDER:
        return trigger_to_realistic_question(trigger)
    text = re.sub(r"^(When|After|While|Before|If)\s+", "", trigger, flags=re.IGNORECASE).strip()
    if text:
        text = text[0].lower() + text[1:]
    return f"I am trying to {text} and ran into a problem. What should I do?"


def check_recall(entity, path, manifest):
    """
    Returns dict:
        passed        – bool: skill is rank-1 or in top-3 with score > 0
        rank          – int or None
        score         – int
        matched_terms – list[str]
        user_msg      – str used for the simulated query
    """
    skill_slug = Path(path).stem
    user_msg = _derive_user_message(entity)
    ranked = _rank_manifest(manifest, user_msg)

    rank = None
    score = 0
    matched = []
    for i, entry in enumerate(ranked):
        if Path(entry["path"]).stem == skill_slug:
            rank = i + 1
            score = entry["score"]
            matched = entry.get("matched_terms", [])
            break

    passed = rank is not None and rank <= 3 and score > 0
    return {
        "passed": passed,
        "rank": rank,
        "score": score,
        "matched_terms": matched,
        "user_msg": user_msg,
    }


# ===========================================================================
# Suite 3 – Skill evaluation (content self-consistency + composition)
# ===========================================================================

def _extract_must_not_include(content):
    patterns = [
        r"does not accept\s+(`[^`]+`|\S+)",
        r"not as a\s+(`[^`]+`|\S+)",
        r"instead of\s+(`[^`]+`|\S+)",
        r"avoid\s+(`[^`]+`|\S+)",
        r"do not (?:use|suggest)\s+(`[^`]+`|\S+)",
        r"not use\s+(`[^`]+`|\S+)",
    ]
    terms = []
    for pat in patterns:
        for m in re.finditer(pat, content, re.IGNORECASE):
            term = m.group(1).strip("`").strip()
            if term:
                terms.append(term)
    return terms


def _build_must_include(content):
    """Derive must_include terms from skill content (mirrors build_expected_behaviour)."""
    if _HAVE_BUILDER:
        dummy_path = Path("dummy.md")
        eb = build_expected_behaviour(dummy_path, {"content": content})
        return eb.get("must_include", [])

    # Minimal fallback
    backtick = re.findall(r"`([^`]+)`", content)
    if backtick:
        return [re.sub(r"\s*<[^>]+>", "", t).strip() for t in backtick if t.strip()]
    candidates = re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", content)
    seen: set = set()
    result = []
    for t in candidates:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result or []


def _normalise_atomic_ref(text):
    text = text.strip().lower()
    text = re.sub(r"\s*`([^`]+)`\s*", r" \1 ", text)
    text = re.sub(r"\s*<[^>]+>\s*", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _trim_action_prefix(text):
    prefixes = (
        "to-",
        "when-",
        "create-",
        "set-up-",
        "install-",
        "configure-",
        "run-",
        "ensure-",
        "import-",
        "activate-",
    )
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _canonical_atomic_skill_refs(entities_dir):
    """Build a lookup of all known atomic skill slugs.

    Searches both the private entities dir and every subscribed clone so that
    skill-flow composition checks pass even after referenced atomic skills have
    been published (moved into the subscribed clone).
    """
    entities_dir = Path(entities_dir)
    # Collect search roots: private dir + all subscribed clones
    search_roots = [entities_dir]
    subscribed_root = entities_dir / "subscribed"
    if subscribed_root.is_dir():
        for clone_dir in subscribed_root.iterdir():
            if clone_dir.is_dir() and (clone_dir / ".git").exists():
                # Published entities land directly under clone_dir/atomic-skill/
                search_roots.append(clone_dir)
                # The clone may also carry its own .evolve/entities/ subtree
                nested = clone_dir / ".evolve" / "entities"
                if nested.is_dir():
                    search_roots.append(nested)

    refs = {}
    for root in search_roots:
        for path in Path(root).glob("**/*.md"):
            if path.is_symlink() or ".git" in path.parts:
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if "atomic-skill" not in rel_parts:
                continue
            try:
                entity = markdown_to_entity(path)
            except Exception:
                continue
            slug = path.stem
            refs[slug] = path
            refs[_normalise_atomic_ref(slug)] = path
            content = entity.get("content", "").strip()
            if content:
                normalized_content = _normalise_atomic_ref(content)
                refs[slugify(content)] = path
                refs[normalized_content] = path
                refs[_trim_action_prefix(normalized_content)] = path

            trigger = entity.get("trigger", "").strip()
            if trigger:
                normalized_trigger = _normalise_atomic_ref(trigger)
                refs[normalized_trigger] = path
                refs[_trim_action_prefix(normalized_trigger)] = path
    return refs


# Minimum fraction of meaningful trigger tokens that must appear in content
_TRIGGER_COVERAGE_THRESHOLD = 0.3


def _trigger_coverage(trigger, content):
    """Return fraction of meaningful trigger tokens found in content text."""
    trig_tok = _slug_tokens(trigger)
    if not trig_tok:
        return 1.0
    content_lower = content.lower()
    found = sum(1 for t in trig_tok if t in content_lower)
    return found / len(trig_tok)


def _check_section_coverage(section_text, entity, section_name):
    """
    Verify that items declared in a metadata section are referenced somewhere
    in the entity's full text (content + rationale + documentation).

    For ## Requirements: each line is a package/tool name.
      - Pip packages: bare name before any version specifier must appear in text.
        e.g. "pyyaml>=6.0" → search for "pyyaml"
      - CLI tools: lines starting with "cli:" strip that prefix before searching.
        e.g. "cli: orchestrate" → search for "orchestrate"
    For ## Imports: each non-empty line is an import statement; at least the
      primary module name or imported symbol must appear in the full text.

    Searches the combined body of: content, rationale, and documentation —
    so a tool named only in the ## Documentation prose still passes.

    Returns list of unmatched declaration strings (empty = all covered).
    """
    if not section_text:
        return []

    # Build a single search corpus from all non-section body parts
    parts = [
        entity.get("content", ""),
        entity.get("rationale", ""),
        entity.get("documentation", ""),
    ]
    corpus = " ".join(p for p in parts if p).lower()
    if not corpus.strip():
        return []

    unmatched = []

    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if section_name == "requirements":
            # Strip "cli: " prefix for CLI tool entries
            normalised = re.sub(r"^cli:\s*", "", line, flags=re.IGNORECASE).strip()
            # Strip version specifiers: "pyyaml>=6.0" → "pyyaml"
            bare = re.split(r"[>=<!;,\s(]", normalised, maxsplit=1)[0].strip().lower()
            # Accept package with hyphens/underscores interchangeable
            bare_alt = bare.replace("-", "_")
            if bare and bare not in corpus and bare_alt not in corpus:
                unmatched.append(line)

        elif section_name == "imports":
            # "import subprocess" → "subprocess"
            # "from pathlib import Path" → "pathlib" or "Path"
            m_from = re.match(r"from\s+(\S+)\s+import\s+(\S+)", line)
            m_imp = re.match(r"import\s+(\S+)", line)
            if m_from:
                module = m_from.group(1).split(".")[0].lower()
                symbol = m_from.group(2).lower()
                if module not in corpus and symbol not in corpus:
                    unmatched.append(line)
            elif m_imp:
                module = m_imp.group(1).split(".")[0].lower()
                if module not in corpus:
                    unmatched.append(line)

    return unmatched


def check_skill_evaluation(entity, entities_dir, atomic_skill_refs=None):
    """
    Returns dict:
        passed              – bool
        alignment_score     – float  (must_include self-consistency)
        trigger_coverage    – float  (fraction of trigger keywords in content)
        matched             – list[str]
        missed              – list[str]
        violated            – list[str]
        trigger_gaps        – list[str]  (trigger keywords absent from content)
        composition_issues  – list[str]  (skill-flow only)
        section_issues      – list[str]  (requirements/imports not in content)
    """
    content = entity.get("content", "")
    trigger = entity.get("trigger", "")

    must_include = _build_must_include(content)
    must_not_include = _extract_must_not_include(content)
    resp_lower = content.lower()

    matched = [t for t in must_include if t.lower() in resp_lower]
    missed = [t for t in must_include if t.lower() not in resp_lower]
    violated = [t for t in must_not_include if t.lower() in resp_lower]

    score = round(len(matched) / len(must_include), 4) if must_include else 1.0
    constraint_ok = not violated
    content_passed = score >= 0.5 and constraint_ok

    # Trigger-coverage check: content must address the trigger's key concepts.
    # This catches skills whose body has drifted away from their stated purpose.
    trig_tok = _slug_tokens(trigger) if trigger else []
    trig_gaps = [t for t in trig_tok if t not in resp_lower]
    trig_cov = round(1.0 - len(trig_gaps) / len(trig_tok), 4) if trig_tok else 1.0
    trigger_passed = trig_cov >= _TRIGGER_COVERAGE_THRESHOLD

    # Section-coverage check: declared requirements and imports must be
    # referenced somewhere in the entity's full text (content + rationale +
    # documentation) so every declared dependency has visible context.
    section_issues: list = []
    for sec in ("requirements", "imports"):
        unmatched = _check_section_coverage(entity.get(sec, ""), entity, sec)
        for item in unmatched:
            section_issues.append(
                f"{sec}: declared '{item}' not referenced in entity text"
            )

    # Composition check for skill-flows
    composition_issues = []
    if entity.get("type") == "skill-flow":
        refs_raw = entity.get("atomic_skills", "")
        refs = [r.strip() for r in refs_raw.split(",") if r.strip()]
        if atomic_skill_refs is None:
            atomic_skill_refs = _canonical_atomic_skill_refs(entities_dir)
        for ref_slug in refs:
            normalized_ref = _normalise_atomic_ref(ref_slug)
            if ref_slug not in atomic_skill_refs and normalized_ref not in atomic_skill_refs:
                composition_issues.append(f"atomic skill not found: '{ref_slug}'")

    passed = content_passed and trigger_passed and not composition_issues and not section_issues
    return {
        "passed": passed,
        "alignment_score": score,
        "trigger_coverage": trig_cov,
        "matched": matched,
        "missed": missed,
        "violated": violated,
        "trigger_gaps": trig_gaps,
        "composition_issues": composition_issues,
        "section_issues": section_issues,
    }


# Only check entities in the owned type subdirectories, not subscribed mirrors
_OWNED_TYPE_DIRS = {"atomic-skill", "guideline", "skill-flow"}


# ===========================================================================
# Orchestrate all suites per entity
# ===========================================================================

def run_quality_gate(entities_dir, manifest, verbose):
    entities_dir = Path(entities_dir)
    md_files = sorted(
        p for p in entities_dir.glob("**/*.md")
        if not p.is_symlink()
        and ".git" not in p.parts
        and "subscribed" not in p.relative_to(entities_dir).parts
        and any(part in _OWNED_TYPE_DIRS for part in p.relative_to(entities_dir).parts)
    )
    if not md_files:
        return []

    atomic_skill_refs = _canonical_atomic_skill_refs(entities_dir)
    results = []
    for path in md_files:
        try:
            entity = markdown_to_entity(path)
        except Exception as exc:
            r = {
                "path": str(path),
                "slug": path.stem,
                "type": None,
                "trigger": "",
                "format": {"issues": [f"could not parse: {exc}"], "passed": False},
                "recall": None,
                "evaluation": None,
                "naming": None,
                "passed": False,
            }
            results.append(r)
            if verbose:
                _print_one(r)
            continue

        fmt_issues = check_format(entity)
        recall = check_recall(entity, path, manifest)
        evaluation = check_skill_evaluation(entity, entities_dir, atomic_skill_refs)
        naming_issues = check_naming(entity, path)
        version_warnings = check_version(entity)

        passed = (
            (not fmt_issues)
            and recall["passed"]
            and evaluation["passed"]
            and (not naming_issues)
            # version_warnings are non-blocking
        )
        r = {
            "path": str(path),
            "slug": path.stem,
            "type": entity.get("type"),
            "trigger": entity.get("trigger", ""),
            "format": {"issues": fmt_issues, "passed": not fmt_issues},
            "recall": recall,
            "evaluation": evaluation,
            "naming": {"issues": naming_issues, "passed": not naming_issues},
            "version": {
                "value": entity.get("version", ""),
                "warnings": version_warnings,
            },
            "passed": passed,
        }
        results.append(r)
        if verbose or not passed:
            _print_one(r)

    return results


# ===========================================================================
# Output helpers
# ===========================================================================

def _print_one(r):
    status = "✅" if r["passed"] else "❌"
    slug = r["slug"]
    parts = []

    fmt = r.get("format", {})
    parts.append("fmt:ok" if fmt.get("passed") else f"fmt:❌({len(fmt.get('issues', []))})")

    rc = r.get("recall")
    if rc:
        parts.append(f"recall:{'ok' if rc['passed'] else '❌'}(rank={rc['rank']},score={rc['score']})")
    else:
        parts.append("recall:skipped")

    ev = r.get("evaluation")
    if ev:
        parts.append(
            f"eval:{'ok' if ev['passed'] else '❌'}"
            f"(align={ev['alignment_score']:.2f},trig={ev['trigger_coverage']:.2f})"
        )
    else:
        parts.append("eval:skipped")

    nm = r.get("naming")
    if nm is not None:
        parts.append("name:ok" if nm.get("passed") else f"name:❌({len(nm.get('issues', []))})")

    vr = r.get("version", {})
    if vr.get("value"):
        v_tag = f"v{vr['value']}"
        v_tag += "⚠" if vr.get("warnings") else ""
        parts.append(v_tag)

    print(f"  {status} {slug:<58}  {'  '.join(parts)}")

    for issue in fmt.get("issues", []):
        print(f"       format  ⚠  {issue}")
    if rc and not rc["passed"]:
        print(f"       recall  ⚠  rank={rc['rank']}  matched={rc['matched_terms']}")
        print(f"                   user_msg: {rc['user_msg'][:80]}")
    if ev:
        if ev["missed"]:
            print(f"       eval    ⚠  missed must_include: {ev['missed']}")
        if ev["violated"]:
            print(f"       eval    ⚠  violated must_not_include: {ev['violated']}")
        # Only surface trigger_gaps when the skill actually failed the trigger-coverage check
        if ev.get("trigger_gaps") and not ev["passed"]:
            print(f"       eval    ⚠  trigger keywords missing from content: {ev['trigger_gaps']}")
        for ci in ev.get("composition_issues", []):
            print(f"       eval    ⚠  composition: {ci}")
        for si in ev.get("section_issues", []):
            print(f"       eval    ⚠  section: {si}")
    if nm and not nm.get("passed"):
        for issue in nm.get("issues", []):
            print(f"       naming  ⚠  {issue}")
    for w in vr.get("warnings", []):
        print(f"       version ⚠  {w}")


def print_summary(results):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    fmt_fail = sum(1 for r in results if not r.get("format", {}).get("passed", True))
    recall_fail = sum(1 for r in results if r.get("recall") and not r["recall"]["passed"])
    eval_fail = sum(1 for r in results if r.get("evaluation") and not r["evaluation"]["passed"])
    naming_fail = sum(1 for r in results if r.get("naming") and not r["naming"]["passed"])
    version_warn = sum(1 for r in results if r.get("version", {}).get("warnings"))

    print()
    print("QUALITY GATE SUMMARY")
    print("=" * 70)

    if passed < total:
        print("Failed skills:")
        for r in results:
            if not r["passed"]:
                _print_one(r)
        print()

    print(f"  Total skills       : {total}")
    print(f"  ✅ Passed           : {passed}")
    print(f"  ❌ Failed           : {total - passed}")
    if fmt_fail:
        print(f"     └ format issues : {fmt_fail}")
    if recall_fail:
        print(f"     └ recall issues : {recall_fail}")
    if eval_fail:
        print(f"     └ eval issues   : {eval_fail}")
    if naming_fail:
        print(f"     └ naming issues : {naming_fail}")
    if version_warn:
        print(f"  ⚠  Version warnings: {version_warn} (non-blocking)")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: run all quality checks before dedup"
    )
    parser.add_argument("--entities-dir", default=None)
    parser.add_argument("--manifest-dir", default=None,
                        help="Build recall manifest from this directory instead of the live .evolve/entities/. "
                             "Use when running quality gate on a merge workspace.")
    parser.add_argument("--report", default=None,
                        help="Write JSON report to this path")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every skill, not just failures")
    parser.add_argument("--local-only", action="store_true",
                        help="Build manifest from private entities only, skipping subscribed/ subdirectories")
    args = parser.parse_args()

    evolve_dir = get_evolve_dir()
    entities_dir = Path(args.entities_dir) if args.entities_dir \
        else evolve_dir / "entities"

    if not entities_dir.exists():
        print(f"No entities directory at {entities_dir}. Nothing to check.")
        sys.exit(0)

    if args.manifest_dir:
        # Use the caller-supplied directory as the recall manifest source
        manifest = dedupe_manifest_entries(load_manifest(Path(args.manifest_dir)))
    elif args.local_only:
        # Build manifest from private entities only — skip subscribed/ clones
        manifest = dedupe_manifest_entries(load_manifest(entities_dir))
    else:
        raw = []
        for root in find_recall_entity_dirs():
            raw.extend(load_manifest(root))
        manifest = dedupe_manifest_entries(raw)

    if not manifest:
        print("Error: recall manifest is empty — no entities loaded.", file=sys.stderr)
        sys.exit(1)

    print(f"Manifest : {len(manifest)} entities")
    print(f"Directory: {entities_dir}")
    print()
    print("QUALITY GATE — Phase 1")
    print("=" * 70)

    results = run_quality_gate(entities_dir, manifest, args.verbose)

    if not results:
        print("No entity files found.")
        sys.exit(0)

    print_summary(results)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "generated_at": datetime.now().isoformat(),
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
            "results": results,
        }
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nReport written: {report_path}")

    all_passed = all(r["passed"] for r in results)
    if not all_passed:
        print("\n❌ Quality gate FAILED — fix issues above before running refine.")
    else:
        print("\n✅ Quality gate PASSED — safe to proceed to Phase 2 (refine).")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()

# Made with Bob
