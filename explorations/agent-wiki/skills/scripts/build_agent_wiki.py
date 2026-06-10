#!/usr/bin/env python3
"""build_agent_wiki.py — single CLI driving the `agent-wiki` family of skills.

Subcommands:
  render-summary       stdin JSON -> summaries/<sid>.md
  render-guidelines    stdin JSON -> guidelines/<slug>__<gid>.md (one per entity)
  render-cluster       stdin JSON -> guidelines/<slug>__cluster.md
  render-task          stdin JSON -> tasks/<slug>.md
  update-config        stdin JSON patch -> wiki-twobatch/_config.yaml
  dump-guidelines      stdout: corpus of atomic guidelines as JSON
  dump-summaries       stdout: corpus of summaries as JSON
  catalog              no input; refresh indexes, _index.jsonl, summary metric frontmatter

The wiki root is found by walking up from cwd looking for an existing
`wiki-twobatch/` directory; if none, it's created next to the nearest
`.git/` ancestor. Pass --wiki-root to override.

`_config.yaml` lives at <wiki_root>/_config.yaml. If absent, catalog copies
the bundled `_default_agent_wiki_config.yaml` (sibling of this script).

Subcommands that mutate are idempotent: re-emit pages with the same content
unless `--rewrite` was passed.

This script is the single deterministic helper for the agent-wiki skill
family — it knows nothing about other plugins. The wiki it produces is
self-contained under <wiki-root>/.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ImportError:
    print("error: PyYAML is required (uv run python ...).", file=sys.stderr)
    raise

WIKI_DIRNAME = "wiki-twobatch"
SUMMARIES_DIR = "summaries"
GUIDELINES_DIR = "guidelines"
TASKS_DIR = "tasks"
SKILLS_DIR = "skills"
ID_INDEX_FILENAME = "_id_index.json"
JSONL_INDEX_FILENAME = "_index.jsonl"
CONFIG_FILENAME = "_config.yaml"
DEFAULT_CONFIG_NAME = "_default_agent_wiki_config.yaml"
SLUG_MAX = 40
ALLOWED_STATUSES = ("followed", "ignored", "contradicted", "harmful")
SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def find_wiki_root(start: Path | None = None, override: Path | None = None) -> Path:
    if override is not None:
        return override.resolve()
    cur = (start or Path.cwd()).resolve()
    base = cur
    while True:
        if (cur / WIKI_DIRNAME).is_dir():
            return cur / WIKI_DIRNAME
        if cur.parent == cur:
            break
        cur = cur.parent
    cur = base
    while True:
        if (cur / ".git").exists():
            return cur / WIKI_DIRNAME
        if cur.parent == cur:
            break
        cur = cur.parent
    return base / WIKI_DIRNAME


def load_config(wiki_root: Path) -> dict:
    p = wiki_root / CONFIG_FILENAME
    if not p.exists():
        seed = SCRIPT_DIR / DEFAULT_CONFIG_NAME
        if seed.exists():
            wiki_root.mkdir(parents=True, exist_ok=True)
            p.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"bootstrapped {p} from {seed.name}", file=sys.stderr)
        else:
            return {"schema_version": 1, "tags": {"guideline": {}}, "clusters": {}, "tasks": {}, "session_family_overrides": {}}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    data.setdefault("tags", {}).setdefault("guideline", {})
    data.setdefault("clusters", {})
    data.setdefault("tasks", {})
    data.setdefault("session_family_overrides", {})
    return data


def save_config(wiki_root: Path, cfg: dict) -> None:
    p = wiki_root / CONFIG_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Slug + id helpers
# ---------------------------------------------------------------------------


_SLUG_NORM = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = SLUG_MAX) -> str:
    s = _SLUG_NORM.sub("-", (text or "").lower()).strip("-")
    if len(s) > max_len:
        cut = s[:max_len]
        last_dash = cut.rfind("-")
        if last_dash >= max_len // 2:
            cut = cut[:last_dash]
        s = cut.rstrip("-")
    return s or "guideline"


def session_prefix(session_id: str | None) -> str:
    """Deprecated: filenames now suffix the guideline content-hash id, not the
    session-id prefix. Retained for one release in case external callers
    still reference it; unused internally.
    """
    if not session_id:
        return "unknown"
    safe = re.sub(r"[^A-Za-z0-9]", "", session_id)
    return safe[:8] or "unknown"


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")


def first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = _SENTENCE_END.search(text)
    if not m:
        return text
    end = m.end() if text[m.start()] in ".!?" else m.start()
    return text[:end].strip()


def compute_entity_id(content: str) -> str:
    norm = " ".join((content or "").lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# YAML scalar / frontmatter
# ---------------------------------------------------------------------------


def yaml_scalar(v: Any) -> str:
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(isinstance(x, str) and "," not in x and " " not in x for x in v):
            return "[" + ", ".join(v) + "]"
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) if isinstance(x, str) else str(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(ch in s for ch in (":", "#", "\n")) or s.startswith(("-", "?", "!", "&", "*", "{", "[", '"', "'")):
        return json.dumps(s, ensure_ascii=False)
    return s


def split_frontmatter(text: str):
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end < 0:
        end = text.find("\n---\r\n", 4)
        if end < 0:
            return None, text
    fm = text[4:end].rstrip()
    body_start = text.find("\n", end + 1) + 1
    return fm, text[body_start:]


def has_top_level_key(fm: str, key: str) -> bool:
    return bool(re.search(rf"^{re.escape(key)}\s*:", fm, re.MULTILINE))


def replace_or_append_field(fm: str, key: str, line: str) -> str:
    # Match the header line PLUS any immediately-following indented child
    # lines (orphans from a previous block-list form). Without the child
    # match, transitioning a value from block-list to inline would leave
    # `  - <item>` lines stranded under the new inline header.
    pat = re.compile(
        rf"^{re.escape(key)}:.*(?:\n[ \t]+.*)*$",
        re.MULTILINE,
    )
    if pat.search(fm):
        return pat.sub(line, fm, count=1)
    return fm.rstrip() + "\n" + line


def append_if_missing(fm: str, key: str, line: str) -> str:
    if has_top_level_key(fm, key):
        return fm
    return fm.rstrip() + "\n" + line


def upsert_fields(text: str, additions: dict, *, force_replace: tuple[str, ...] = ()) -> str:
    """Append/replace YAML fields. Existing keys NOT in `force_replace` are left alone."""
    fm, body = split_frontmatter(text)
    if fm is None:
        new = ["---"]
        for k, v in additions.items():
            new.extend(_emit_yaml_field(k, v))
        new.append("---")
        new.append("")
        return "\n".join(new) + body

    for k, v in additions.items():
        line = _emit_yaml_field(k, v)
        if len(line) == 1 and k in force_replace:
            fm = replace_or_append_field(fm, k, line[0])
        elif len(line) == 1:
            fm = append_if_missing(fm, k, line[0])
        else:
            # block list — replace via regex if forced, else append if missing
            if has_top_level_key(fm, k):
                if k in force_replace:
                    fm = _replace_block_field(fm, k, line)
                # else: leave existing
            else:
                fm = fm.rstrip() + "\n" + "\n".join(line)
    return "---\n" + fm + "\n---\n" + body


def _emit_yaml_field(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        if all(isinstance(x, str) for x in value) and all("," not in x and len(x) < 60 for x in value):
            return [f"{key}: " + yaml_scalar(value)]
        # block list (strings or dicts)
        out = [f"{key}:"]
        for item in value:
            if isinstance(item, dict):
                first = True
                for kk, vv in item.items():
                    prefix = "  - " if first else "    "
                    out.append(f"{prefix}{kk}: {yaml_scalar(vv)}")
                    first = False
            else:
                out.append(f"  - {item}")
        return out
    return [f"{key}: {yaml_scalar(value)}"]


def _replace_block_field(fm: str, key: str, lines: list[str]) -> str:
    """Replace a top-level block field (key: + indented children)."""
    pat = re.compile(
        rf"^{re.escape(key)}:.*?(?=^\S|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    repl = "\n".join(lines) + "\n"
    if pat.search(fm):
        return pat.sub(repl, fm, count=1)
    return fm.rstrip() + "\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Subcommand: render-summary
# ---------------------------------------------------------------------------


SUMMARY_REQUIRED = ("session_id", "narrative", "normalized_path")


def cmd_render_summary(args) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("error: top-level JSON must be an object", file=sys.stderr)
        return 2
    missing = [k for k in SUMMARY_REQUIRED if not data.get(k)]
    if missing:
        print(f"error: missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    wiki_root = find_wiki_root(override=args.wiki_root)
    out_dir = wiki_root / SUMMARIES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    session_id = str(data["session_id"])
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", session_id).strip("-") or "session"
    # Optional `slug`/`arc` field splits a single session into multiple summary
    # files. Filename pattern: `<sid>__<slug>.md`. Without a slug, fall back to
    # the historical `<sid>.md` (single-summary-per-session) shape.
    arc_slug = (data.get("slug") or data.get("arc") or "").strip()
    if arc_slug:
        arc_slug = slugify(arc_slug, max_len=50)
        out_name = f"{safe}__{arc_slug}.md"
    else:
        out_name = f"{safe}.md"
    out_path = out_dir / out_name

    if out_path.exists() and not args.rewrite:
        print(f"skip (exists): {out_path}")
        return 0

    # load id_index for backlink resolution
    id_index = _load_id_index(wiki_root)
    recalled = _normalize_recalled(data.get("recalled_guidelines"), id_index)
    # discover sibling arc summaries for this session_id (other files matching
    # `<sid>*__*.md`). Excluded: this file itself.
    siblings = sorted(p.name for p in out_dir.glob(f"{safe}__*.md") if p.name != out_name)
    out_path.write_text(_render_summary_md(data, recalled, arc_slug=arc_slug, siblings=siblings), encoding="utf-8")
    print(f"wrote: {out_path}")

    if recalled:
        for r in recalled:
            _audit_append(
                wiki_root,
                {
                    "action": "summary.guideline_use",
                    "session_id": session_id,
                    "id": r["id"],
                    "status": r["status"],
                },
            )
        print(f"audit: {len(recalled)} line(s) appended to {wiki_root}/_audit.log")
    return 0


def _normalize_recalled(items: Any, id_index: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = (item.get("id") or "").strip()
        if not eid:
            continue
        status = (item.get("status") or "").strip().lower() or "ignored"
        if status not in ALLOWED_STATUSES:
            status = "ignored"
        out.append(
            {
                "id": eid,
                "title": (item.get("title") or "").strip() or eid,
                "status": status,
                "evidence": (item.get("evidence") or "").strip(),
                "link": id_index.get(eid),
            }
        )
    return out


def _render_summary_md(summary: dict, recalled: list[dict], arc_slug: str = "", siblings: list[str] | None = None) -> str:
    fm = ["---", "type: episodic-summary"]
    for k in ("session_id", "agent", "model", "goal", "outcome"):
        v = summary.get(k)
        if v is not None:
            fm.append(f"{k}: {yaml_scalar(v)}")
    if arc_slug:
        fm.append(f"arc: {yaml_scalar(arc_slug)}")
    if (d := summary.get("duration_seconds")) is not None:
        fm.append(f"duration_seconds: {d}")
    tools = summary.get("tools_used") or []
    if tools:
        fm.append("tools_used: [" + ", ".join(yaml_scalar(t) for t in tools) + "]")
    sources: list[str] = []
    np = summary.get("normalized_path")
    tp = summary.get("transcript_path")
    if np:
        sources.append(np)
    if tp and tp != np:
        sources.append(tp)
    if sources:
        fm.append("sources:")
        for s in sources:
            fm.append(f"  - {s}")
    if siblings:
        fm.append("sibling_summaries:")
        for s in siblings:
            fm.append(f"  - {s}")
    if recalled:
        fm.append("recalled_guidelines:")
        for r in recalled:
            fm.append(f"  - id: {r['id']}")
            fm.append(f"    title: {yaml_scalar(r['title'])}")
            fm.append(f"    status: {r['status']}")
            if r.get("evidence"):
                fm.append(f"    evidence: {yaml_scalar(r['evidence'])}")
            if r.get("link"):
                fm.append(f"    link: {r['link']}")
    fm.append("---")
    fm.append("")

    body: list[str] = []
    title = summary.get("goal") or f"Session {summary.get('session_id', '')}"
    body.append(f"# {title}")
    body.append("")
    narrative = (summary.get("narrative") or "").strip()
    if narrative:
        body.append(narrative)
        body.append("")
    key_turns = summary.get("key_turns") or []
    if key_turns:
        body.append("## Key turns")
        body.append("")
        for kt in key_turns:
            body.append(f"- {kt}")
        body.append("")
    if recalled:
        body.append("## Recalled guidelines")
        body.append("")
        for r in recalled:
            label = r["title"]
            link = r.get("link")
            if link:
                label = f"[{label}](../{link})"
            line = f"- **{r['status']}** — {label}"
            if r.get("evidence"):
                line += f' — "{r["evidence"]}"'
            body.append(line)
        body.append("")
    if sources:
        body.append("## Sources")
        body.append("")
        if np:
            body.append(f"- [normalized JSON]({np})")
        if tp and tp != np:
            body.append(f"- raw transcript: `{tp}`")
        body.append("")
    return "\n".join(fm + body)


def _load_id_index(wiki_root: Path) -> dict[str, str]:
    p = wiki_root / GUIDELINES_DIR / ID_INDEX_FILENAME
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _audit_append(wiki_root: Path, entry: dict) -> None:
    """Append one JSON line to <wiki-root>/_audit.log. Self-contained per wiki."""
    p = wiki_root / "_audit.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    full = {**entry, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(full, ensure_ascii=False) + "\n")


_ARCHIVED_DIR = "_archived"

# Tags that appear on so many atomics they're not useful for "covers" inference.
# A skill whose tags overlap an atomic's *only* via these is NOT considered to cover it.
_GENERIC_TAGS = {
    "stdlib",
    "parsing",
    "agent-behavior",
    "contract",
    "fallback-avoidance",
    "wiki-pointer",
    "wiki-scope",
    "applicability",
    "operator-side",
    "agent-side",
    "binary",
    "headers",
}


def _archive_atomic(wiki_root: Path, gid: str, reason: str, target_slug: str) -> bool:
    """Move an atomic guideline to <wiki>/_archived/<filename>.
    Drop the gid from `_id_index.json`. Append an `archive_guideline` audit entry.
    No-op (returns False) if the gid isn't in the id index or the file is missing.
    """
    idx_path = wiki_root / GUIDELINES_DIR / ID_INDEX_FILENAME
    if not idx_path.exists():
        return False
    try:
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if gid not in idx:
        return False
    rel = idx.pop(gid)
    src = wiki_root / rel
    if not src.is_file():
        return False
    dst_dir = wiki_root / _ARCHIVED_DIR
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    src.rename(dst)
    idx_path.write_text(json.dumps(idx, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _audit_append(
        wiki_root,
        {
            "action": "archive_guideline",
            "id": gid,
            "reason": reason,
            "target": target_slug,
            "src": rel,
            "dst": f"{_ARCHIVED_DIR}/{src.name}",
        },
    )
    return True


# Slug/title tokens too generic to be evidence of coverage on their own.
# A skill slug containing "python" must not archive every atomic whose title
# happens to say "Python". These words appear across unrelated guidelines, so
# a lexical match on one of them carries no signal.
_GENERIC_SLUG_TOKENS = {
    "read",
    "extract",
    "count",
    "list",
    "via",
    "from",
    "the",
    "for",
    "with",
    "of",
    "a",
    "an",
    "and",
    "or",
    "to",
    "on",
    "in",
    "by",
    "into",
    "python",
    "python3",
    "script",
    "scripts",
    "scripting",
    "file",
    "files",
    "data",
    "command",
    "commands",
    "run",
    "running",
    "write",
    "writing",
    "system",
    "use",
    "using",
    "fix",
    "fixing",
    "parse",
    "parsing",
    "json",
    "install",
    "installing",
    "verify",
    "container",
    "containers",
    "docker",
}


def _session_of_atomic(info: dict) -> str:
    """Extract the source session id from an atomic's scan info.
    `related_summary` is `summaries/<sid>.md` or `summaries/<sid>__<arc>.md`.
    Returns '' if not derivable.
    """
    rel = (info or {}).get("related_summary") or ""
    m = re.search(r"summaries/([^/]+?)(?:__[^/]+)?\.md\s*$", rel)
    return m.group(1) if m else ""


def _skill_covers_atomic(
    skill_tags: set[str],
    skill_slug: str,
    atomic_tags: set[str],
    atomic_title: str,
    skill_description: str = "",
    *,
    same_session: bool = False,
) -> bool:
    """Inference: does this skill cover this atomic guideline?

    Three paths to True, split by how strong the signal is:

    1. **Tag-superset path** (works cross-trajectory): skill's tags are a
       superset of the atomic's tags AND their intersection contains ≥ 2
       non-generic tags. This is the disciplined signal — a true tag
       superset means the skill's topic genuinely subsumes the atomic's.

    2. **Slug-keyword path** (same-session only): a *distinctive* token
       (≥ 4 chars, not in `_GENERIC_SLUG_TOKENS`) from the skill slug
       appears in the atomic's title.

    3. **Format-token path** (same-session only): an all-caps/CamelCase
       format identifier (e.g. "PNG", "JPEG", "WebP", "CSV") shared between
       the skill's description and the atomic's title.

    Paths 2 and 3 are weak lexical heuristics — a single shared word. They
    only fire when the atomic was extracted from the *same trajectory* the
    skill was synthesized from (`same_session=True`), where any topical
    overlap is real coverage rather than coincidence. Cross-trajectory
    archival requires the strong Path 1. This prevents a skill from reaching
    across into an unrelated trace's atomics on an incidental word match.

    Bias: false negatives are safe (atomic stays); false positives are
    expensive (atomic incorrectly archived).
    """
    if not atomic_tags:
        atomic_tags = set()
    # Path 1: superset + ≥2 non-generic shared tags (cross-trajectory OK)
    if atomic_tags and atomic_tags <= skill_tags:
        non_generic = (skill_tags & atomic_tags) - _GENERIC_TAGS
        if len(non_generic) >= 2:
            return True
    # Paths 2 & 3 are weak lexical signals — only trust them within the same
    # trajectory. A skill cannot archive a different trace's atomic on these.
    if not same_session:
        return False
    title_lc = atomic_title.lower()
    # Path 2: distinctive slug-keyword in title
    skill_tokens = {t for t in skill_slug.split("-") if t not in _GENERIC_SLUG_TOKENS and len(t) >= 4}
    for tok in skill_tokens:
        if tok in title_lc:
            return True
    # Path 3: format/identifier token shared between skill description and atomic title
    format_tokens = re.findall(r"\b([A-Z]{3,}|[A-Z][a-z]+[A-Z][a-zP]+)\b", skill_description or "")
    format_tokens = {t for t in format_tokens if t not in {"AND", "OR", "THE", "USE"}}
    for tok in format_tokens:
        if tok.lower() in title_lc:
            return True
    return False


# ---------------------------------------------------------------------------
# Subcommand: render-guidelines
# ---------------------------------------------------------------------------


def cmd_render_guidelines(args) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("error: top-level JSON must be an object with `entities`", file=sys.stderr)
        return 2
    entities = data.get("entities") or []
    if not isinstance(entities, list) or not entities:
        print("no entities provided; nothing to write", file=sys.stderr)
        return 0

    wiki_root = find_wiki_root(override=args.wiki_root)
    out_dir = wiki_root / GUIDELINES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    new_index: dict[str, str] = {}
    cfg = load_config(wiki_root)
    cfg_tag_map = cfg.setdefault("tags", {}).setdefault("guideline", {})
    cfg_dirty = False
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        content = (entity.get("content") or "").strip()
        if not content:
            continue
        sid = entity.get("session_id") or args.session_id
        norm = entity.get("normalized_path") or entity.get("trajectory") or args.normalized_path
        eid = (entity.get("id") or "").strip() or compute_entity_id(content)
        slug_source = entity.get("slug") or entity.get("title") or content
        slug = slugify(slug_source)
        # Filename suffix is the guideline's content-hash id, NOT the
        # session-id prefix. Two motivations: filename ↔ `id:` frontmatter
        # round-trip cleanly, and two guidelines from the same session no
        # longer share a suffix. Session lineage stays recoverable via the
        # `related_summary:` frontmatter and the `## Sources` footer.
        out_path = out_dir / f"{slug}__{eid}.md"
        # Persist tags into _config.yaml so the catalog "By tag" table picks
        # them up. Authored entries in cfg win over re-extractions.
        ent_tags = entity.get("tags") or []
        if isinstance(ent_tags, list):
            ent_tags = [str(t).strip() for t in ent_tags if str(t).strip()]
            if ent_tags and (eid not in cfg_tag_map or args.rewrite):
                cfg_tag_map[eid] = ent_tags
                cfg_dirty = True
        if out_path.exists() and not args.rewrite:
            print(f"skip (exists): {out_path}")
            skipped += 1
            new_index[eid] = f"{GUIDELINES_DIR}/{out_path.name}"
            continue
        out_path.write_text(_render_guideline_md(entity, norm, sid, eid), encoding="utf-8")
        new_index[eid] = f"{GUIDELINES_DIR}/{out_path.name}"
        print(f"wrote: {out_path}")
        written += 1
    _update_id_index(out_dir, new_index)
    if cfg_dirty:
        save_config(wiki_root, cfg)
    print(f"\nwrote {written}, skipped {skipped}")
    return 0


def _render_guideline_md(entity: dict, normalized_path: str | None, session_id: str | None, eid: str) -> str:
    content = (entity.get("content") or "").strip()
    rationale = (entity.get("rationale") or "").strip()
    trigger = (entity.get("trigger") or "").strip()
    title = (entity.get("title") or "").strip() or first_sentence(content) or "Guideline"
    etype = entity.get("type") or "guideline"

    # Optional `arc` lets the entity bind to one specific arc-summary when the
    # session has been split into multiple arc-files. Filename pattern is
    # `<sid>__<arc>.md`; if `arc` is empty, fall back to `<sid>.md`.
    arc = (entity.get("arc") or "").strip()
    if arc:
        arc = slugify(arc, max_len=50)
    summary_basename = f"{session_id}__{arc}.md" if (session_id and arc) else (f"{session_id}.md" if session_id else "")

    fm = ["---", f"id: {eid}", f"type: {etype}"]
    if trigger:
        fm.append(f"trigger: {yaml_scalar(trigger)}")
    fm.append(f"agent: {entity.get('agent') or 'claude-code'}")
    tags = entity.get("tags") or []
    if isinstance(tags, list):
        tags_clean = [str(t).strip() for t in tags if str(t).strip()]
        if tags_clean:
            fm.append(f"tags: [{', '.join(tags_clean)}]")
    sources: list[str] = []
    if normalized_path:
        sources.append(normalized_path)
    if sources:
        fm.append("sources:")
        for s in sources:
            fm.append(f"  - {s}")
    if summary_basename:
        fm.append(f"related_summary: {SUMMARIES_DIR}/{summary_basename}")
    fm.append("---")
    fm.append("")
    body = [f"# {title}", "", content, ""]
    if rationale:
        body.extend(["## Rationale", "", rationale, ""])
    body.append("## Sources")
    body.append("")
    if summary_basename:
        body.append(f"- [trajectory summary](../{SUMMARIES_DIR}/{summary_basename})")
    if normalized_path:
        body.append(f"- [normalized JSON]({normalized_path})")
    body.append("")
    return "\n".join(fm + body)


def _update_id_index(out_dir: Path, entries: dict[str, str]) -> None:
    if not entries:
        return
    p = out_dir / ID_INDEX_FILENAME
    cur: dict = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(cur, dict):
                cur = {}
        except (OSError, json.JSONDecodeError):
            cur = {}
    cur.update(entries)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=ID_INDEX_FILENAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _missing_jsonl_index_links(wiki_root: Path) -> list[tuple[str, str, str]]:
    idx = wiki_root / "_index.jsonl"
    if not idx.exists():
        return []

    missing: list[tuple[str, str, str]] = []
    for line_no, line in enumerate(idx.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{idx}:{line_no}: invalid JSON: {exc}") from exc
        link = row.get("link")
        if link and not (wiki_root / link).exists():
            missing.append((str(row.get("kind") or ""), str(row.get("id") or ""), str(link)))
    return missing


def _assert_jsonl_index_integrity(wiki_root: Path) -> None:
    missing = _missing_jsonl_index_links(wiki_root)
    if not missing:
        return
    details = "; ".join(f"{kind}:{ident} -> {link}" for kind, ident, link in missing[:10])
    extra = f"; +{len(missing) - 10} more" if len(missing) > 10 else ""
    raise RuntimeError(f"{wiki_root / '_index.jsonl'} has missing links: {details}{extra}")


def _refresh_agent_retrieval_indexes(wiki_root: Path) -> None:
    """Refresh indexes agents use immediately after local page moves/writes."""
    cfg = load_config(wiki_root)
    today = datetime.date.today().isoformat()
    g_meta = _scan_atomic_guidelines(wiki_root)
    _write_guidelines_index(wiki_root, g_meta, cfg, today)
    _write_skills_index(wiki_root, _scan_skills(wiki_root), today)
    _write_jsonl_index(wiki_root, cfg, g_meta)
    _assert_jsonl_index_integrity(wiki_root)


# ---------------------------------------------------------------------------
# Subcommand: render-cluster
# ---------------------------------------------------------------------------


def cmd_render_cluster(args) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("error: top-level JSON must be an object", file=sys.stderr)
        return 2
    slug = (data.get("slug") or "").strip()
    if not slug:
        print("error: missing slug", file=sys.stderr)
        return 2

    wiki_root = find_wiki_root(override=args.wiki_root)
    cfg = load_config(wiki_root)

    # write/update config
    members = data.get("members") or []
    cfg["clusters"][slug] = {
        "title": data.get("title") or slug,
        "description": data.get("description") or "",
        "takeaway": data.get("takeaway") or "",
        "members": members,
        "tags": data.get("tags") or [],
    }
    save_config(wiki_root, cfg)

    # render the cluster page
    out_path = wiki_root / GUIDELINES_DIR / f"{slug}__cluster.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_cluster_md(slug, cfg["clusters"][slug], wiki_root), encoding="utf-8")
    print(f"wrote: {out_path}")

    # Archive each member atomic — the cluster page now represents them.
    if getattr(args, "archive_members", False):
        archived = 0
        for gid in members:
            if _archive_atomic(wiki_root, gid, reason="covered_by_cluster", target_slug=slug):
                archived += 1
        if archived:
            print(f"  archived {archived} member atomic(s) to {_ARCHIVED_DIR}/")
    _refresh_agent_retrieval_indexes(wiki_root)
    print("refreshed: skills/index.md, guidelines/index.md, _index.jsonl")
    return 0


def _render_cluster_md(slug: str, info: dict, wiki_root: Path) -> str:
    today = datetime.date.today().isoformat()
    members = info.get("members") or []
    id_index = _load_id_index(wiki_root)

    fm = [
        "---",
        "type: cluster",
        f"slug: {slug}",
        f"title: {yaml_scalar(info.get('title') or slug)}",
        "tags: " + yaml_scalar(info.get("tags") or []),
        f"verified_at: {today}",
        "members:",
    ]
    for gid in members:
        link = id_index.get(gid)
        fm.append(f"  - id: {gid}")
        if link:
            # cluster page lives in guidelines/, so relative link is just basename
            fm.append(f"    link: {Path(link).name}")
    fm.append("priority: high")
    fm.append("---")
    fm.append("")

    body = [f"# {info.get('title') or slug}", "", info.get("description") or "", ""]
    takeaway = (info.get("takeaway") or "").strip()
    if takeaway:
        body.extend(["## Takeaway", "", takeaway, ""])
    body.append("## Members")
    body.append("")
    body.append(
        "These guidelines are kept as separate pages for full provenance back to "
        "their source trajectories. The cluster references them; nothing is moved "
        "or merged."
    )
    body.append("")
    for gid in members:
        link = id_index.get(gid)
        title, snippet, trigger, related = _read_guideline_meta(wiki_root, link) if link else (gid, "", "", "")
        body.append(f"### [{title}]({Path(link).name if link else gid})")
        body.append("")
        body.append(f"- **id:** `{gid}`")
        if trigger:
            body.append(f"- **trigger:** {trigger}")
        if related:
            body.append(f"- **source:** [{related.replace('summaries/', '')[:14]}](../{related})")
        if snippet:
            body.append("")
            body.append(f"> {snippet}")
        body.append("")
    return "\n".join(fm + body)


def _read_guideline_meta(wiki_root: Path, relpath: str) -> tuple[str, str, str, str]:
    p = wiki_root / relpath
    if not p.exists():
        return relpath, "", "", ""
    text = p.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    title_m = re.search(r"^# (.+)$", body or "", re.MULTILINE)
    title = title_m.group(1).strip() if title_m else relpath
    trig_m = re.search(r"^trigger:\s*(.+)$", fm or "", re.MULTILINE)
    trigger = trig_m.group(1).strip() if trig_m else ""
    if trigger.startswith('"') and trigger.endswith('"'):
        try:
            trigger = json.loads(trigger)
        except Exception:
            pass
    rel_m = re.search(r"^related_summary:\s*(.+)$", fm or "", re.MULTILINE)
    related = rel_m.group(1).strip() if rel_m else ""
    cm = re.search(r"^# .+?\n\n(.+?)(?=\n\n|\n## |\Z)", body or "", re.S | re.M)
    snippet = cm.group(1).replace("\n", " ").strip() if cm else ""
    if len(snippet) > 300:
        snippet = snippet[:300].rsplit(" ", 1)[0] + "…"
    return title, snippet, trigger, related


# ---------------------------------------------------------------------------
# Subcommand: render-task
# ---------------------------------------------------------------------------


def cmd_render_task(args) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    slug = (data.get("slug") or "").strip()
    if not slug:
        print("error: missing slug", file=sys.stderr)
        return 2

    wiki_root = find_wiki_root(override=args.wiki_root)
    cfg = load_config(wiki_root)
    cfg["tasks"][slug] = {
        "title": data.get("title") or slug,
        "family": data.get("family") or slug,
        "family_match": data.get("family_match") or {},
        "intro": data.get("intro") or "",
        "findings": data.get("findings") or "",
        "tags": data.get("tags") or [],
    }
    save_config(wiki_root, cfg)

    out = wiki_root / TASKS_DIR / f"{slug}__task.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_task_md(slug, cfg["tasks"][slug], wiki_root, cfg), encoding="utf-8")
    print(f"wrote: {out}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: render-skill
# ---------------------------------------------------------------------------


SKILL_REQUIRED = ("name", "description", "workflow_steps")
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_SKILL_FILENAME_RE = re.compile(r"^[\w][\w.-]*$")


def cmd_render_skill(args) -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("error: top-level JSON must be an object", file=sys.stderr)
        return 2
    missing = [k for k in SKILL_REQUIRED if not data.get(k)]
    if missing:
        print(f"error: missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    name = str(data["name"]).strip()
    if not _SKILL_NAME_RE.match(name):
        print(f"error: name {name!r} is not kebab-case", file=sys.stderr)
        return 2

    wiki_root = find_wiki_root(override=args.wiki_root)
    out_dir = wiki_root / SKILLS_DIR / name
    skill_md = out_dir / "SKILL.md"
    if skill_md.exists() and not args.rewrite:
        print(f"skip (exists): {skill_md}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    # Author SKILL.md
    today = datetime.date.today().isoformat()
    desc = str(data["description"]).strip()
    trigger = (data.get("trigger") or "").strip()
    sid = (data.get("session_id") or "").strip()
    related = (data.get("related_summary") or "").strip()
    if not related and sid:
        related = f"{SUMMARIES_DIR}/{sid}.md"
    norm_path = (data.get("normalized_path") or "").strip()
    agent_id = (data.get("agent") or "claude-code").strip()
    tags = data.get("tags") or []

    fm = ["---"]
    fm.append(f"id: skill:{name}")
    fm.append("type: skill")
    fm.append(f"name: {name}")
    fm.append(f"description: {yaml_scalar(desc)}")
    if trigger:
        fm.append(f"trigger: {yaml_scalar(trigger)}")
    fm.append(f"agent: {agent_id}")
    if norm_path:
        fm.append("sources:")
        fm.append(f"  - {norm_path}")
    if related:
        fm.append(f"related_summary: {related}")
    fm.append(f"verified_at: {today}")
    if tags:
        fm.append("tags: [" + ", ".join(yaml_scalar(t) for t in tags) + "]")
    fm.append("---")
    fm.append("")

    # Body
    body: list[str] = []
    title = data.get("title") or name.replace("-", " ").title()
    body.append(f"# {title}")
    body.append("")
    overview = (data.get("overview") or desc).strip()
    body.append("## Overview")
    body.append("")
    body.append(overview)
    body.append("")

    when_to_use = data.get("when_to_use") or []
    if when_to_use:
        body.append("## When To Use")
        body.append("")
        for line in when_to_use:
            body.append(f"- {line}")
        body.append("")

    workflow = data.get("workflow_steps") or []
    body.append("## Workflow")
    body.append("")
    for i, step in enumerate(workflow, start=1):
        body.append(f"{i}. {step}")
    body.append("")

    # Sources footer
    body.append("## Sources")
    body.append("")
    if related:
        body.append(f"- [trajectory summary](../../{related})")
    if norm_path:
        body.append(f"- [normalized JSON]({norm_path})")
    body.append("")

    skill_md.write_text("\n".join(fm + body), encoding="utf-8")
    print(f"wrote: {skill_md}")

    # Sibling scripts
    scripts = data.get("scripts") or []
    scripts_dir = out_dir / "scripts"
    written_scripts: list[str] = []
    for s in scripts:
        if not isinstance(s, dict):
            continue
        sname = (s.get("name") or "").strip()
        scontent = s.get("content")
        if not sname or not scontent:
            continue
        if not _SKILL_FILENAME_RE.match(sname):
            print(f"warning: skipping invalid script name {sname!r}", file=sys.stderr)
            continue
        scripts_dir.mkdir(parents=True, exist_ok=True)
        sp = scripts_dir / sname
        sp.write_text(scontent, encoding="utf-8")
        if sname.endswith((".sh", ".bash")):
            sp.chmod(0o755)
        written_scripts.append(sname)
        print(f"  + {sp}")

    # Update _id_index.json
    idx_path = wiki_root / SKILLS_DIR / ID_INDEX_FILENAME
    idx = {}
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            idx = {}
    idx[name] = f"{SKILLS_DIR}/{name}/SKILL.md"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text(json.dumps(idx, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Audit
    if sid:
        _audit_append(
            wiki_root,
            {
                "action": "synthesize_skill",
                "session_id": sid,
                "skill_name": name,
                "scripts": written_scripts,
            },
        )
        print(f"audit: synthesize_skill recorded for {name}")

    # Archive atomic guidelines this skill covers (delete-on-promote).
    # Cross-trajectory archival uses only the disciplined tag-superset path;
    # the weak lexical paths (slug-token, format-token) fire only for atomics
    # from the SAME trajectory this skill was synthesized from. See
    # `_skill_covers_atomic` for the rationale.
    if getattr(args, "archive_covered", False):
        skill_tags = set(tags or [])
        archived: list[str] = []
        # _scan_atomic_guidelines is defined later in the file but we can reach it.
        for gid, info in _scan_atomic_guidelines(wiki_root).items():
            atomic_tags = set(info.get("tags") or [])
            atomic_title = info.get("title") or ""
            same_session = bool(sid) and _session_of_atomic(info) == sid
            if _skill_covers_atomic(skill_tags, name, atomic_tags, atomic_title, desc, same_session=same_session):
                if _archive_atomic(wiki_root, gid, reason="covered_by_skill", target_slug=name):
                    archived.append(gid)
        if archived:
            print(f"  archived {len(archived)} covered atomic(s): {', '.join(archived)}")
    _refresh_agent_retrieval_indexes(wiki_root)
    print("refreshed: skills/index.md, guidelines/index.md, _index.jsonl")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: render-subtask
# ---------------------------------------------------------------------------


def cmd_render_subtask(args) -> int:
    """Render a subtask page: a narrative slice within a single session.

    Stdin JSON:
      {
        "slug":             "<kebab-case identifier>",
        "title":            "<short title>",
        "parent_session_id":"<session_id, full UUID>",
        "parent_summary":   "<filename of parent summary in summaries/, e.g. abc123__arc1.md>",
        "tags":             ["..."],
        "narrative":        "<one-or-two paragraphs>",
        "key_steps":        ["...", "..."]    # optional
      }
    """
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    slug = (data.get("slug") or "").strip()
    if not slug:
        print("error: missing slug", file=sys.stderr)
        return 2
    parent_summary = (data.get("parent_summary") or "").strip()
    parent_sid = (data.get("parent_session_id") or "").strip()
    if not parent_summary and not parent_sid:
        print("error: subtask requires parent_summary or parent_session_id", file=sys.stderr)
        return 2
    wiki_root = find_wiki_root(override=args.wiki_root)
    out_dir = wiki_root / TASKS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}__subtask.md"
    today = datetime.date.today().isoformat()

    title = data.get("title") or slug
    tags = data.get("tags") or []
    narrative = (data.get("narrative") or "").strip()
    key_steps = data.get("key_steps") or []

    fm = ["---", "type: subtask", f"slug: {slug}", f"title: {yaml_scalar(title)}"]
    if parent_sid:
        fm.append(f"parent_session_id: {parent_sid}")
    if parent_summary:
        fm.append(f"parent_summary: {SUMMARIES_DIR}/{parent_summary}")
    fm.append("tags: " + yaml_scalar(tags))
    fm.append(f"verified_at: {today}")
    fm.append("---")
    fm.append("")

    body = [f"# {title}", ""]
    if narrative:
        body.append(narrative)
        body.append("")
    if key_steps:
        body.append("## Key steps")
        body.append("")
        for s in key_steps:
            body.append(f"- {s}")
        body.append("")
    if parent_summary:
        body.append("## Parent summary")
        body.append("")
        body.append(f"- [{parent_summary}](../{SUMMARIES_DIR}/{parent_summary})")
        body.append("")

    out.write_text("\n".join(fm + body), encoding="utf-8")
    print(f"wrote: {out}")
    return 0


def _render_task_md(slug: str, info: dict, wiki_root: Path, cfg: dict) -> str:
    today = datetime.date.today().isoformat()
    sessions = _classify_sessions(wiki_root, cfg)
    rows = sorted([s for s in sessions if s["family"] == info.get("family")], key=lambda x: (x.get("condition") or "", x.get("trial") or 0))

    fm = [
        "---",
        "type: task-comparison",
        f"slug: {slug}",
        f"title: {yaml_scalar(info.get('title') or slug)}",
        "tags: " + yaml_scalar(info.get("tags") or []),
        f"verified_at: {today}",
        f"sessions: {len(rows)}",
        "---",
        "",
    ]
    body: list[str] = [f"# {info.get('title') or slug}", ""]
    intro = (info.get("intro") or "").strip()
    if intro:
        body.append(intro)
        body.append("")
    body.append("## Comparison")
    body.append("")
    body.append("| Trial | Condition | Session | Tool calls | Errors | Wiki used | Contributed guidelines |")
    body.append("|-------|-----------|---------|-----------:|-------:|:------:|------------------------|")
    for s in rows:
        sid = s["session_id"]
        sid_short = sid[:8]
        # Prefer the per-session summary basename when this session has only one
        # summary; if it was split into arcs, point at the first arc and let the
        # reader navigate from there via sibling_summaries.
        summary_basename = s.get("summary_basename") or f"{sid}.md"
        link = f"../{SUMMARIES_DIR}/{summary_basename}"
        tc = s.get("tool_calls") or 0
        err = s.get("errors") or 0
        recall = "Y" if s.get("wiki_consulted") else "—"
        contrib = ", ".join(f"`{x}`" for x in (s.get("contributed_guidelines") or [])) or "—"
        trial = str(s.get("trial") or "—")
        cond = s.get("condition") or "—"
        body.append(f"| {trial} | {cond} | [{sid_short}…]({link}) | {tc} | {err} | {recall} | {contrib} |")
    body.append("")
    findings = (info.get("findings") or "").strip()
    if findings:
        body.extend(["## Findings", "", findings, ""])
    return "\n".join(fm + body)


# ---------------------------------------------------------------------------
# Session classification (shared helper)
# ---------------------------------------------------------------------------


def _classify_sessions(wiki_root: Path, cfg: dict) -> list[dict]:
    overrides = cfg.get("session_family_overrides") or {}
    tasks_cfg = cfg.get("tasks") or {}
    sessions = []
    summaries_dir = wiki_root / SUMMARIES_DIR
    if not summaries_dir.is_dir():
        return sessions
    for p in sorted(summaries_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        sid_m = re.search(r"^session_id:\s*(.+)$", fm, re.MULTILINE)
        goal_m = re.search(r"^goal:\s*(.+)$", fm, re.MULTILINE)
        sources = re.findall(r"^  - (\S+)", fm, re.MULTILINE)
        sid = sid_m.group(1).strip() if sid_m else p.stem
        goal = goal_m.group(1).strip() if goal_m else ""
        if goal.startswith('"') and goal.endswith('"'):
            try:
                goal = json.loads(goal)
            except Exception:
                pass
        np = sources[0] if sources else ""
        path_haystack = " ".join(sources)

        family, trial, condition = _classify_one(sid, goal, path_haystack, overrides, tasks_cfg)

        # metrics from existing fm (added by catalog)
        def fm_int(key):
            m = re.search(rf"^{key}:\s*(\d+)\s*$", fm, re.MULTILINE)
            return int(m.group(1)) if m else None

        def fm_float(key):
            m = re.search(rf"^{key}:\s*([\d.]+)\s*$", fm, re.MULTILINE)
            return float(m.group(1)) if m else None

        def fm_bool(key):
            m = re.search(rf"^{key}:\s*(true|false)\s*$", fm, re.MULTILINE)
            return m.group(1) == "true" if m else None

        def fm_list(key):
            # inline list: key: [a, b]
            m = re.search(rf"^{key}:\s*\[(.*?)\]\s*$", fm, re.MULTILINE)
            if m:
                items = [x.strip() for x in m.group(1).split(",") if x.strip()]
                return items
            # block list: key:\n  - a\n  - b
            m = re.search(rf"^{key}:\s*\n((?:  - .+\n)+)", fm, re.MULTILINE)
            if m:
                return [line[4:].strip() for line in m.group(1).splitlines() if line.startswith("  - ")]
            return []

        arc_m = re.search(r"^arc:\s*(.+)$", fm, re.MULTILINE)
        arc = arc_m.group(1).strip() if arc_m else ""
        if arc.startswith('"') and arc.endswith('"'):
            try:
                arc = json.loads(arc)
            except Exception:
                pass

        sessions.append(
            {
                "session_id": sid,
                "arc": arc,
                "goal": goal,
                "normalized_path": np,
                "family": family,
                "trial": trial,
                "condition": condition,
                "tool_calls": fm_int("tool_calls"),
                "errors": fm_int("errors"),
                "wiki_consulted": fm_bool("wiki_consulted"),
                "contributed_guidelines": fm_list("contributed_guidelines"),
                "contributed_skills": fm_list("contributed_skills"),
                "input_tokens": fm_int("input_tokens"),
                "cache_creation_input_tokens": fm_int("cache_creation_input_tokens"),
                "cache_read_input_tokens": fm_int("cache_read_input_tokens"),
                "output_tokens": fm_int("output_tokens"),
                "total_cost_usd": fm_float("total_cost_usd"),
                "summary_path": p,
                "summary_basename": p.name,
            }
        )
    return sessions


def _classify_one(sid: str, goal: str, np: str, overrides: dict, tasks_cfg: dict) -> tuple[str | None, int | None, str | None]:
    if sid in overrides:
        o = overrides[sid] or {}
        return o.get("family"), o.get("trial"), o.get("condition")
    g = goal.lower()
    family = None
    for slug, info in tasks_cfg.items():
        match = (info or {}).get("family_match") or {}
        for sub in match.get("goal_substring") or []:
            if sub.lower() in g:
                family = info.get("family") or slug
                break
        if family:
            break
    trial = None
    cond = None
    m = re.search(r"trial_(\d+)_(seed|no_recall|guidelines|skill)", np)
    if m:
        trial = int(m.group(1))
        cond = m.group(2).replace("_", "-")
    return family, trial, cond


# ---------------------------------------------------------------------------
# Subcommand: update-config (patch)
# ---------------------------------------------------------------------------


def cmd_update_config(args) -> int:
    try:
        patch = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2
    if not isinstance(patch, dict):
        print("error: top-level JSON must be an object", file=sys.stderr)
        return 2
    wiki_root = find_wiki_root(override=args.wiki_root)
    cfg = load_config(wiki_root)
    _deep_merge(cfg, patch)
    save_config(wiki_root, cfg)
    print(f"updated: {wiki_root / CONFIG_FILENAME}")
    return 0


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


# ---------------------------------------------------------------------------
# Subcommand: dump-guidelines
# ---------------------------------------------------------------------------


def cmd_dump_guidelines(args) -> int:
    wiki_root = find_wiki_root(override=args.wiki_root)
    out = []
    g_dir = wiki_root / GUIDELINES_DIR
    if not g_dir.is_dir():
        print("[]", end="")
        return 0
    for p in sorted(g_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        gid_m = re.search(r"^id:\s*(\S+)", fm, re.MULTILINE)
        title_m = re.search(r"^# (.+)$", body or "", re.MULTILINE)
        trig_m = re.search(r"^trigger:\s*(.+)$", fm, re.MULTILINE)
        cluster_m = re.search(r"^cluster:\s*(.+)$", fm, re.MULTILINE)
        cm = re.search(r"^# .+?\n\n(.+?)(?=\n\n|\n## |\Z)", body or "", re.S | re.M)
        out.append(
            {
                "id": gid_m.group(1).strip() if gid_m else compute_entity_id(body or ""),
                "filename": p.name,
                "title": (title_m.group(1).strip() if title_m else ""),
                "trigger": (trig_m.group(1).strip() if trig_m else ""),
                "cluster": (cluster_m.group(1).strip() if cluster_m else None),
                "is_cluster_page": p.name.endswith("__cluster.md"),
                "content": (cm.group(1).strip() if cm else ""),
            }
        )
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: dump-summaries
# ---------------------------------------------------------------------------


def cmd_dump_summaries(args) -> int:
    wiki_root = find_wiki_root(override=args.wiki_root)
    cfg = load_config(wiki_root)
    sessions = _classify_sessions(wiki_root, cfg)
    out = []
    for s in sessions:
        out.append(
            {
                "session_id": s["session_id"],
                "goal": s["goal"],
                "family": s["family"],
                "trial": s["trial"],
                "condition": s["condition"],
                "tool_calls": s["tool_calls"],
                "errors": s["errors"],
                "wiki_consulted": s["wiki_consulted"],
                "summary_filename": s["summary_path"].name,
            }
        )
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: catalog (the big bookkeeping pass)
# ---------------------------------------------------------------------------


def cmd_catalog(args) -> int:
    wiki_root = find_wiki_root(override=args.wiki_root)
    wiki_root.mkdir(parents=True, exist_ok=True)
    cfg = load_config(wiki_root)
    today = datetime.date.today().isoformat()

    # Phase 0: bootstrap AGENTS.md from the bundled template if absent.
    # Subsequent runs leave a present file alone — the user owns AGENTS.md
    # after first bootstrap.
    agents_path = wiki_root / "AGENTS.md"
    if not agents_path.exists():
        seed = SCRIPT_DIR / "_default_agents.md"
        if seed.exists():
            agents_path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"bootstrapped {agents_path} from {seed.name}", file=sys.stderr)

    # Phase 1: enrich atomic guideline frontmatter from config
    g_meta = _scan_atomic_guidelines(wiki_root)
    tag_map = (cfg.get("tags") or {}).get("guideline") or {}
    cluster_map = {}
    for slug, info in (cfg.get("clusters") or {}).items():
        for gid in (info or {}).get("members") or []:
            cluster_map[gid] = slug

    summaries_dir = wiki_root / SUMMARIES_DIR
    enriched = 0
    repaired_links = 0
    for gid, info in g_meta.items():
        text = info["path"].read_text(encoding="utf-8")
        additions: dict[str, Any] = {"verified_at": today}
        if tag_map.get(gid):
            additions["tags"] = list(tag_map[gid])
        cluster_slug = cluster_map.get(gid)
        if cluster_slug:
            additions["cluster"] = f"{cluster_slug}__cluster.md"
            additions["superseded_by"] = f"{cluster_slug}__cluster.md"
        # Auto-repair dangling `related_summary:` when the linked file is
        # missing AND the session has arc-split summaries on disk. Picks the
        # first arc lex-sorted; emits a stderr warning so the user can override
        # by editing the frontmatter directly.
        related = (info.get("related_summary") or "").strip()
        if related and summaries_dir.is_dir():
            related_basename = Path(related).name
            if not (summaries_dir / related_basename).exists():
                # try to find sibling arc-summaries for this session_id
                sid_stem = related_basename.removesuffix(".md")
                candidates = sorted(p.name for p in summaries_dir.glob(f"{sid_stem}__*.md"))
                if candidates:
                    new_related = f"{SUMMARIES_DIR}/{candidates[0]}"
                    additions["related_summary"] = new_related
                    if len(candidates) > 1:
                        print(
                            f"warning: {info['path'].name} pointed at missing {related_basename}; "
                            f"repaired to {candidates[0]} (one of {len(candidates)} arc-summaries — "
                            f"override via the frontmatter if a different arc is canonical)",
                            file=sys.stderr,
                        )
                    repaired_links += 1
        new_text = upsert_fields(text, additions, force_replace=("verified_at", "tags", "cluster", "superseded_by", "related_summary"))
        # If we repaired related_summary, also fix the body link.
        if "related_summary" in additions:
            old_link = f"../{SUMMARIES_DIR}/{Path(related).name}"
            new_link = f"../{additions['related_summary']}"
            new_text = new_text.replace(old_link, new_link)
        if new_text != text:
            info["path"].write_text(new_text, encoding="utf-8")
            enriched += 1
    if repaired_links:
        print(f"repaired {repaired_links} dangling related_summary link(s)")

    # Phase 2: regenerate cluster pages from config
    clusters_written = 0
    for slug, info in (cfg.get("clusters") or {}).items():
        out = wiki_root / GUIDELINES_DIR / f"{slug}__cluster.md"
        out.write_text(_render_cluster_md(slug, info or {}, wiki_root), encoding="utf-8")
        clusters_written += 1

    # Phase 3: enrich summary frontmatter (metrics + tags + contributed_guidelines).
    # Must happen BEFORE task pages render — _render_task_md reads enriched
    # frontmatter (tool_calls, wiki_consulted, contributed_guidelines) via
    # _classify_sessions; running it post-enrichment avoids stale zeros.
    enriched_summaries = _enrich_summaries(wiki_root, cfg, g_meta, today)

    # Phase 3b: inject/refresh `## Used by` section on each atomic guideline
    # by inverting the `recalled_guidelines:` blocks across all summaries.
    used_by_updated = _inject_used_by_sections(wiki_root, g_meta)

    # Phase 4: regenerate task pages from config (suffix: __task.md)
    tasks_written = 0
    for slug, info in (cfg.get("tasks") or {}).items():
        out = wiki_root / TASKS_DIR / f"{slug}__task.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_render_task_md(slug, info or {}, wiki_root, cfg), encoding="utf-8")
        tasks_written += 1
        # Migration: clean up legacy <slug>.md from a prior version of this script.
        legacy = wiki_root / TASKS_DIR / f"{slug}.md"
        if legacy.exists() and legacy != out:
            legacy.unlink()

    # Phase 5: regenerate index pages.
    # Re-classify summaries here (after Phase 4 enrichment) so the index
    # reflects the just-written tool_calls / wiki_consulted /
    # contributed_guidelines frontmatter rather than pre-enrichment zeros.
    sessions = _classify_sessions(wiki_root, cfg)
    _write_root_index(wiki_root, cfg, g_meta, sessions, today)
    _write_summaries_index(wiki_root, sessions, today)
    _write_guidelines_index(wiki_root, g_meta, cfg, today)
    _write_tasks_index(wiki_root, cfg, today)
    _write_skills_index(wiki_root, _scan_skills(wiki_root), today)

    # Phase 6: regenerate _index.jsonl
    _write_jsonl_index(wiki_root, cfg, g_meta)
    _assert_jsonl_index_integrity(wiki_root)

    print(
        f"catalog: enriched {enriched} guideline(s), wrote {clusters_written} cluster page(s), "
        f"{tasks_written} task page(s), enriched {enriched_summaries} summary file(s), "
        f"{used_by_updated} used-by section(s) updated"
    )
    return 0


def _scan_atomic_guidelines(wiki_root: Path) -> dict[str, dict]:
    """Return {id: {path, relpath, title, trigger, first_para, related_summary}} for atomic guidelines.

    Atomic = NOT a `__cluster.md` file. Reads `id:` from frontmatter, falling
    back to a content-derived id.
    """
    out: dict[str, dict] = {}
    g_dir = wiki_root / GUIDELINES_DIR
    if not g_dir.is_dir():
        return out
    for p in sorted(g_dir.glob("*.md")):
        if p.name == "index.md" or p.name.endswith("__cluster.md"):
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        gid_m = re.search(r"^id:\s*(\S+)", fm, re.MULTILINE)
        if gid_m:
            gid = gid_m.group(1).strip()
        else:
            cm = re.search(r"^# .+?\n\n(.+?)(?=\n\n|\n## |\Z)", body or "", re.S | re.M)
            gid = compute_entity_id(cm.group(1) if cm else (body or ""))
        title_m = re.search(r"^# (.+)$", body or "", re.MULTILINE)
        trig_m = re.search(r"^trigger:\s*(.+)$", fm, re.MULTILINE)
        rel_m = re.search(r"^related_summary:\s*(.+)$", fm, re.MULTILINE)
        ver_m = re.search(r"^verified_at:\s*(.+)$", fm, re.MULTILINE)
        tags_m = re.search(r"^tags:\s*\[(.*?)\]\s*$", fm, re.MULTILINE)
        tags_list = [t.strip() for t in (tags_m.group(1).split(",") if tags_m else []) if t.strip()] if tags_m else []
        cm = re.search(r"^# .+?\n\n(.+?)(?=\n\n|\n## |\Z)", body or "", re.S | re.M)
        out[gid] = {
            "path": p,
            "relpath": f"{GUIDELINES_DIR}/{p.name}",
            "title": title_m.group(1).strip() if title_m else p.name,
            "trigger": trig_m.group(1).strip() if trig_m else "",
            "first_para": (cm.group(1).replace("\n", " ").strip() if cm else "")[:240],
            "related_summary": rel_m.group(1).strip() if rel_m else "",
            "verified_at": ver_m.group(1).strip() if ver_m else "",
            "tags": tags_list,
        }
    # also persist _id_index.json with current state
    id_index = {gid: info["relpath"] for gid, info in out.items()}
    if id_index:
        _update_id_index(g_dir, id_index)
    return out


_CONDITION_IN_GOAL_RE = re.compile(r"(?<![A-Za-z0-9_])([a-z][a-z0-9_]+)/trial-\d+", re.IGNORECASE)


def _extract_condition(goal: str, fm_data: dict) -> str:
    """Return the trial condition slug for a summary, or '' if not detectable.
    Prefers explicit frontmatter `condition:`; falls back to a `<slug>/trial-N`
    pattern in the goal text (matches authoring conventions like
    'claude_md_strong/trial-1', 'session_hook/trial-2').
    """
    explicit = fm_data.get("condition")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if not isinstance(goal, str):
        return ""
    m = _CONDITION_IN_GOAL_RE.search(goal)
    return m.group(1) if m else ""


def _scan_recalled_guidelines_in_summaries(wiki_root: Path) -> dict[str, list[dict]]:
    """Build {gid -> [{summary_basename, summary_title, condition, status, evidence}]}
    by parsing every summary's frontmatter via PyYAML.
    """
    out: dict[str, list[dict]] = {}
    summaries_dir = wiki_root / SUMMARIES_DIR
    if not summaries_dir.is_dir():
        return out
    for p in sorted(summaries_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8")
        fm, _ = split_frontmatter(text)
        if fm is None:
            continue
        try:
            data = yaml.safe_load(fm) or {}
        except yaml.YAMLError:
            continue
        rgs = data.get("recalled_guidelines") or []
        summary_title = data.get("goal") or p.stem
        condition = _extract_condition(data.get("goal") or "", data)
        for entry in rgs:
            if not isinstance(entry, dict):
                continue
            gid = str(entry.get("id") or "").strip()
            if not gid:
                continue
            status = str(entry.get("status") or "ignored").strip().lower()
            evidence = entry.get("evidence")
            evidence = evidence.strip() if isinstance(evidence, str) else ""
            out.setdefault(gid, []).append(
                {
                    "summary_basename": p.name,
                    "summary_title": summary_title,
                    "condition": condition,
                    "status": status,
                    "evidence": evidence,
                }
            )
    return out


_USED_BY_RE = re.compile(
    r"\n## Used by\n.*?(?=\n## |\Z)",
    re.DOTALL,
)


def _inject_used_by_sections(wiki_root: Path, g_meta: dict) -> int:
    """For each atomic guideline page, render or refresh a `## Used by`
    section listing every summary whose `recalled_guidelines:` references
    this guideline, with per-session condition / status / evidence quote.
    Idempotent: an existing section is replaced, not duplicated.

    Always rendered, even when no recalls exist — pages without recalls show
    `_(no recalls yet)_` so readers can distinguish 'never recalled' from
    'old wiki, missing section'. Contributing the guideline does not count
    as a use; only frontmatter `recalled_guidelines:` entries count.

    Returns count of guideline pages updated.
    """
    usages = _scan_recalled_guidelines_in_summaries(wiki_root)
    updated = 0
    for gid, info in g_meta.items():
        rows = usages.get(gid) or []
        rows = sorted(rows, key=lambda r: r["summary_basename"])

        lines = ["", "## Used by", ""]
        if rows:
            lines.append("| Session | Condition | Status | Evidence |")
            lines.append("|---------|-----------|--------|----------|")
            for r in rows:
                sid_short = r["summary_basename"].split(".md")[0][:18]
                cond = r.get("condition") or "—"
                ev = (r.get("evidence") or "").replace("|", "\\|").replace("\n", " ").strip()
                if len(ev) > 200:
                    ev = ev[:197] + "…"
                ev_cell = f'"{ev}"' if ev else "—"
                lines.append(f"| [{sid_short}…](../{SUMMARIES_DIR}/{r['summary_basename']}) | `{cond}` | **{r['status']}** | {ev_cell} |")
        else:
            lines.append("_(no recalls yet)_")
        new_section = "\n".join(lines) + "\n"

        text = info["path"].read_text(encoding="utf-8")
        # Remove any existing ## Used by section (idempotent)
        without = _USED_BY_RE.sub("", text)
        # Insert the fresh section right before ## Sources, or at end if no Sources.
        if "\n## Sources" in without:
            new_text = without.replace("\n## Sources", new_section + "\n## Sources", 1)
        else:
            new_text = without.rstrip() + new_section
        if new_text != text:
            info["path"].write_text(new_text, encoding="utf-8")
            updated += 1
    return updated


def _enrich_summaries(wiki_root: Path, cfg: dict, g_meta: dict, today: str) -> int:
    """Compute metrics from normalized JSON; add `contributed_guidelines:` and
    `contributed_skills:` from inverted related_summary on guidelines/ + skills/.
    """
    # invert: summary_basename (without .md) -> [guideline_ids] from each
    # guideline's related_summary. The basename keys the lookup so an
    # arc-summary like `<sid>__arc1.md` only collects guidelines that
    # explicitly bind to that arc.
    basename_to_gids: dict[str, list[str]] = {}
    for gid, info in g_meta.items():
        rel = info.get("related_summary") or ""
        m = re.match(rf"^{SUMMARIES_DIR}/(.+)\.md$", rel)
        if m:
            basename_to_gids.setdefault(m.group(1), []).append(gid)

    # Same inversion across <wiki>/skills/ — by skill slug, not content hash.
    basename_to_skills: dict[str, list[str]] = {}
    for slug, sk in _scan_skills(wiki_root).items():
        rel = sk.get("related_summary") or ""
        m = re.match(rf"^{SUMMARIES_DIR}/(.+)\.md$", rel)
        if m:
            basename_to_skills.setdefault(m.group(1), []).append(slug)

    repo_root = _repo_root(wiki_root)
    enriched = 0
    summaries_dir = wiki_root / SUMMARIES_DIR
    if not summaries_dir.is_dir():
        return 0
    overrides = cfg.get("session_family_overrides") or {}
    tasks_cfg = cfg.get("tasks") or {}
    for p in sorted(summaries_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        text = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        sid_m = re.search(r"^session_id:\s*(.+)$", fm, re.MULTILINE)
        sid = sid_m.group(1).strip() if sid_m else p.stem
        sources_list = re.findall(r"^  - (\S+)", fm, re.MULTILINE)
        np_rel = sources_list[0] if sources_list else ""
        path_haystack = " ".join(sources_list)
        goal_m = re.search(r"^goal:\s*(.+)$", fm, re.MULTILINE)
        goal = goal_m.group(1).strip() if goal_m else ""
        if goal.startswith('"') and goal.endswith('"'):
            try:
                goal = json.loads(goal)
            except Exception:
                pass

        metrics = _compute_metrics(np_rel, repo_root)
        family, _, _ = _classify_one(sid, goal, path_haystack, overrides, tasks_cfg)
        tags = _summary_tags(goal, path_haystack, family, metrics["wiki_consulted"])
        # Look up by the summary file's basename (sans .md) so arc-summaries
        # only collect guidelines that bound to that specific arc.
        contributed = basename_to_gids.get(p.stem, [])
        contributed_skills = basename_to_skills.get(p.stem, [])

        additions = {
            "tags": tags,
            "tool_calls": metrics["tool_calls"],
            "errors": metrics["errors"],
            "dead_end_paths": metrics["dead_end_paths"],
            "wiki_consulted": metrics["wiki_consulted"],
            "contributed_guidelines": contributed,
            "contributed_skills": contributed_skills,
            "verified_at": today,
        }
        # Token + cost fields. Skip whichever are zero — saves clutter on
        # summaries that lack the data (older normalized JSONs without the
        # new stats fields, or sessions where the result event had no usage).
        if metrics["input_tokens"] or metrics["cache_creation_input_tokens"] or metrics["output_tokens"]:
            additions["input_tokens"] = metrics["input_tokens"]
            additions["cache_creation_input_tokens"] = metrics["cache_creation_input_tokens"]
            additions["cache_read_input_tokens"] = metrics["cache_read_input_tokens"]
            additions["output_tokens"] = metrics["output_tokens"]
        if metrics["total_cost_usd"]:
            # Round to 4 decimals so frontmatter stays readable.
            additions["total_cost_usd"] = round(float(metrics["total_cost_usd"]), 4)
        new_text = upsert_fields(
            text,
            additions,
            force_replace=(
                "tags",
                "tool_calls",
                "errors",
                "dead_end_paths",
                "wiki_consulted",
                "contributed_guidelines",
                "contributed_skills",
                "verified_at",
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "output_tokens",
                "total_cost_usd",
            ),
        )
        # One-shot migration: drop the legacy `recall_used:` field if present.
        new_text = re.sub(r"^recall_used:.*\n", "", new_text, count=1, flags=re.MULTILINE)
        if new_text != text:
            p.write_text(new_text, encoding="utf-8")
            enriched += 1
    return enriched


def _repo_root(wiki_root: Path) -> Path:
    cur = wiki_root.parent
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return wiki_root.parent
        cur = cur.parent


def _compute_metrics(np_rel: str, repo_root: Path) -> dict:
    out = {
        "tool_calls": 0,
        "errors": 0,
        "dead_end_paths": 0,
        "wiki_consulted": False,
        "input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
        "total_cost_usd": 0.0,
    }
    if not np_rel:
        return out
    p = Path(np_rel)
    candidates = [p if p.is_absolute() else repo_root / np_rel]
    # if relative path doesn't exist, try wiki ancestor too
    json_path = next((c for c in candidates if c.exists()), None)
    if not json_path:
        return out
    try:
        d = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    msgs = (d.get("openai_chat_completion") or {}).get("messages") or []
    stats = d.get("stats") or {}
    out["tool_calls"] = stats.get("tool_call_count", 0)
    errs = sum(1 for m in msgs if m.get("role") == "tool" and m.get("is_error"))
    out["errors"] = errs
    out["dead_end_paths"] = errs

    # Token + cost from stats (preferred; populated by the updated normalizer).
    # Fallback: walk per-message `usage` blocks. Cost USD has no fallback —
    # only the original `result` event carries it.
    if "input_tokens" in stats:
        out["input_tokens"] = int(stats.get("input_tokens") or 0)
        out["cache_creation_input_tokens"] = int(stats.get("cache_creation_input_tokens") or 0)
        out["cache_read_input_tokens"] = int(stats.get("cache_read_input_tokens") or 0)
        out["output_tokens"] = int(stats.get("output_tokens") or 0)
    else:
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            usage = m.get("usage") or {}
            out["input_tokens"] += int(usage.get("input_tokens") or 0)
            out["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
            out["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
            out["output_tokens"] += int(usage.get("output_tokens") or 0)
    out["total_cost_usd"] = float(stats.get("total_cost_usd") or 0.0)
    # `wiki_consulted`: did the agent read any wiki guideline/AGENTS.md page?
    # Detect via Read tool calls or Bash commands containing wiki-shaped paths
    # (`/AGENTS.md` or `/guidelines/<slug>__<gid>.md`). agent-wiki has
    # no knowledge of any other recall layer.
    wiki_consulted = False
    wiki_path_pat = re.compile(r"AGENTS\.md|guidelines/[A-Za-z0-9_./-]+\.md")
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments", "")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                continue
            name = fn.get("name", "")
            if name == "Read":
                if wiki_path_pat.search(str(args.get("file_path", ""))):
                    wiki_consulted = True
            elif name == "Bash":
                if wiki_path_pat.search(str(args.get("command", ""))):
                    wiki_consulted = True
    out["wiki_consulted"] = wiki_consulted
    return out


def _summary_tags(goal: str, np_rel: str, family: str | None, wiki_consulted: bool) -> list[str]:
    tags: list[str] = []
    g = goal.lower()
    if family:
        tags.append(family)
    if "exif" in g or "exif" in np_rel.lower():
        tags.append("exif")
    if "focal length" in g:
        tags.append("focal-length")
    if "lens model" in g or "lens" in g:
        tags.append("lens-model")
    if "where was" in g or "gps" in g:
        tags.append("gps")
    if "synthesize" in g:
        tags.append("synthesize-skill")
    m = re.search(r"trial_(\d+)_(seed|no_recall|guidelines|skill)", np_rel)
    if m:
        tags.append(f"trial-{m.group(1)}")
        tags.append(f"condition-{m.group(2).replace('_', '-')}")
    if wiki_consulted:
        tags.append("wiki-consulted")
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out


# ---------------------------------------------------------------------------
# Index page writers
# ---------------------------------------------------------------------------


def _write_root_index(wiki_root: Path, cfg: dict, g_meta: dict, sessions: list[dict], today: str) -> None:
    n_clusters = len(cfg.get("clusters") or {})
    n_tasks = len(cfg.get("tasks") or {})
    n_subtasks = len(_scan_subtasks(wiki_root / TASKS_DIR))
    n_atomic = len(g_meta)
    multi_arc_sessions = len({s["session_id"] for s in sessions if sum(1 for x in sessions if x["session_id"] == s["session_id"]) > 1})
    summary_blurb = f"{len(sessions)} pages"
    if multi_arc_sessions:
        summary_blurb += f" ({multi_arc_sessions} session(s) split across multiple arc-summaries)"
    lines = [
        "---",
        "type: wiki-index",
        f"verified_at: {today}",
        "---",
        "",
        f"# {wiki_root.name}",
        "",
        "An evidence-grounded wiki of agent trajectories: each lesson links back to "
        "the trajectory that produced it. Built by the `agent-wiki` skill family from "
        "normalized agent transcripts.",
        "",
        "## Sections",
        "",
        f"- [Tasks](tasks/index.md) — `__task.md` cross-session comparisons ({n_tasks}) "
        f"+ `__subtask.md` per-session workstreams ({n_subtasks})",
        f"- [Guidelines](guidelines/index.md) — atomic lessons + cluster aggregator pages "
        f"(suffix `__cluster.md`); cluster pages are recall-preferred ({n_atomic} atomic + {n_clusters} clusters)",
        f"- [Summaries](summaries/index.md) — episodic summaries ({summary_blurb}). "
        f"Long sessions may be split into multiple arc-summaries that share a `session_id`.",
        "",
        "## How content relates",
        "",
        "```",
        "raw .jsonl  ──normalize──▶  normalized JSON  ──summarize──▶  summary",
        "                                                                │",
        "                                                                └──▶  guideline (one or more)  ──cluster──▶  guideline (cluster) page",
        "                                                                                                              │",
        "                            task comparison page  ◀───────────────────────────────────────────────────────────┘",
        "```",
        "",
        "Provenance closes via:",
        "",
        "- `summary.contributed_guidelines: [id, …]` (outbound)",
        "- `guideline.related_summary: summaries/<sid>.md` (inbound)",
        "- `guideline.cluster: <slug>__cluster.md` (themed group)",
        "- `cluster.members[].link: <member>.md` (preserves originals)",
        "- `_index.jsonl` at the wiki root for cheap filter+score retrieval",
        "",
        "## For agents (recall-time)",
        "",
        "Read [_index.jsonl](_index.jsonl) — one row per guideline + cluster page with "
        "`{id, kind, title, tags, trigger, summary, link}`. Filter by tag, score on "
        "trigger overlap, then follow `link` for the full content.",
        "",
        "## Cluster pages",
        "",
        "Cluster pages live in `guidelines/` with the `__cluster.md` suffix. They are "
        "themed aggregators that reference atomic-guideline siblings — the originals "
        "stay intact. At recall time clusters are preferred over their members; atomic "
        "members carry a `superseded_by:` field.",
        "",
        "## Staleness",
        "",
        f"All pages stamp `verified_at`. Today: **{today}**. Pages without an "
        "`expires_at` are valid until a follow-up trajectory contradicts them.",
        "",
    ]
    (wiki_root / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_summaries_index(wiki_root: Path, sessions: list[dict], today: str) -> None:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for s in sessions:
        key = (s.get("family") or "other", s.get("condition") or "unknown")
        grouped.setdefault(key, []).append(s)

    # Sessions split into multiple arc-summaries (same session_id across N files)
    by_sid: dict[str, list[dict]] = {}
    for s in sessions:
        by_sid.setdefault(s["session_id"], []).append(s)
    multi_arc = {sid: rows for sid, rows in by_sid.items() if len(rows) > 1}

    lines = [
        "---",
        "type: section-index",
        "section: summaries",
        f"verified_at: {today}",
        f"count: {len(sessions)}",
        "---",
        "",
        "# Summaries",
        "",
        "One episodic summary per trajectory (or per arc, when a long session "
        "is split into multiple arc-summaries that share a `session_id`). "
        "See [../tasks/](../tasks/index.md) for cross-session comparisons "
        "and intra-session subtasks.",
        "",
    ]
    if multi_arc:
        lines.append("## Sessions split across multiple arc-summaries")
        lines.append("")
        lines.append(
            "These rows share a `session_id` but live in separate files. Each "
            "carries `arc:` plus a `sibling_summaries:` list pointing at the others."
        )
        lines.append("")
        for sid, rows in sorted(multi_arc.items()):
            lines.append(f"- **`{sid[:8]}…`** — {len(rows)} arcs:")
            for r in sorted(rows, key=lambda x: x.get("arc") or x["summary_basename"]):
                arc = r.get("arc") or "(no arc tag)"
                lines.append(f"  - [{arc}]({r['summary_basename']})")
        lines.append("")
    for key in sorted(grouped.keys()):
        fam, cond = key
        rows = sorted(grouped[key], key=lambda x: x.get("trial") or 0)
        lines.append(f"## `{fam}` / `{cond}` ({len(rows)})")
        lines.append("")
        lines.append("| Trial | Session | Arc | Tool calls | Errors | Wiki used | Contributed guidelines | Contributed skills | Cost USD |")
        lines.append("|------:|---------|-----|-----------:|-------:|:------:|------------------------|--------------------|---------:|")
        for s in rows:
            sid = s["session_id"]
            arc = s.get("arc") or "—"
            tc = s.get("tool_calls") or 0
            err = s.get("errors") or 0
            recall = "Y" if s.get("wiki_consulted") else "—"
            contrib = ", ".join(f"`{x}`" for x in (s.get("contributed_guidelines") or [])) or "—"
            skills = ", ".join(f"`{x}`" for x in (s.get("contributed_skills") or [])) or "—"
            trial = str(s.get("trial") or "—")
            cost = s.get("total_cost_usd")
            cost_cell = f"${cost:.4f}" if cost else "—"
            lines.append(
                f"| {trial} | [{sid[:8]}…]({s['summary_path'].name}) | {arc} | {tc} | {err} | {recall} | {contrib} | {skills} | {cost_cell} |"
            )
        lines.append("")
    (wiki_root / SUMMARIES_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


_PRIORITY_TIERS = ("high", "disputed", "weak", "normal", "low", "unvalidated")


def _compute_priority(*, kind: str, is_cluster_member: bool, counts: dict) -> str:
    """Multi-factor priority assignment. See `_PRIORITY_TIERS` for the order
    `## Pages, by priority` sorts rows in. Six tiers, computed from recall
    counts + cluster membership; not authored. Recomputed on every catalog.
    """
    if kind == "cluster":
        return "high"
    f = counts.get("followed", 0)
    i = counts.get("ignored", 0)
    c = counts.get("contradicted", 0)
    h = counts.get("harmful", 0)
    has_neg = (c > 0) or (h > 0)
    if f > 0 and has_neg:
        return "disputed"  # mixed signals — investigate
    if has_neg:
        return "weak"  # neg-only — candidate to deprecate
    if f >= 5:
        return "high"  # strongly validated
    if is_cluster_member:
        return "low"  # cluster supersedes routine atomics
    if f + i == 0:
        return "unvalidated"
    return "normal"


def _render_priority_table(*, g_meta: dict, clusters: dict, tag_map: dict, cluster_for_id: dict, usages: dict, today: str) -> list[str]:
    """Build the `## Pages, by priority` table. Returns a list of markdown
    lines (caller appends to its own `lines` buffer).
    """
    rows: list[dict] = []

    # Cluster rows
    for slug, info in (clusters or {}).items():
        info = info or {}
        rows.append(
            {
                "title": info.get("title") or slug,
                "link": f"{slug}__cluster.md",
                "kind": "cluster",
                "priority": _compute_priority(kind="cluster", is_cluster_member=False, counts={}),
                "trigger": "—",
                "tags": ", ".join(info.get("tags") or []) or "—",
                "cluster": "—",
                "counts": {s: 0 for s in ALLOWED_STATUSES},
                "verified_at": today,
            }
        )

    # Atomic rows
    for gid, info in g_meta.items():
        cs = cluster_for_id.get(gid)
        recalls = usages.get(gid) or []
        counts = {s: 0 for s in ALLOWED_STATUSES}
        for r in recalls:
            st = r["status"]
            if st in counts:
                counts[st] += 1
        rows.append(
            {
                "title": info["title"],
                "link": Path(info["relpath"]).name,
                "kind": "atomic",
                "priority": _compute_priority(
                    kind="atomic",
                    is_cluster_member=bool(cs),
                    counts=counts,
                ),
                "trigger": (info.get("trigger") or "—").strip() or "—",
                "tags": ", ".join(tag_map.get(gid) or []) or "—",
                "cluster": cs or "—",
                "counts": counts,
                "verified_at": info.get("verified_at") or today,
            }
        )

    tier_order = {t: i for i, t in enumerate(_PRIORITY_TIERS)}
    rows.sort(key=lambda r: (tier_order.get(r["priority"], 99), r["title"].lower()))

    out = ["## Pages, by priority", ""]
    out.append(
        "Unified roll-up across clusters + atomic guidelines. Priority is "
        "computed each catalog run from recall counts and cluster membership "
        "(not authored). Rows sort by tier "
        "(`high` → `disputed` → `weak` → `normal` → `low` → `unvalidated`), "
        "then alphabetical within tier."
    )
    out.append("")
    out.append("| Title | Kind | Priority | Trigger | Tags | Cluster | Recall (T / f / i / c / h) | Verified at |")
    out.append("|-------|------|----------|---------|------|---------|---------------------------:|-------------|")
    for r in rows:
        c = r["counts"]
        total = sum(c.values())
        recall_cell = (
            "—" if r["kind"] == "cluster" else (f"{total} / {c['followed']} / {c['ignored']} / {c['contradicted']} / {c['harmful']}")
        )
        # Pipe-escape and truncate trigger to keep the row scannable.
        trig = r["trigger"].replace("|", "\\|").replace("\n", " ").strip()
        if len(trig) > 80:
            trig = trig[:77] + "…"
        out.append(
            f"| [{r['title']}]({r['link']}) | {r['kind']} | **{r['priority']}** "
            f"| {trig} | {r['tags']} | {r['cluster']} | {recall_cell} | {r['verified_at']} |"
        )
    out.append("")
    return out


def _write_guidelines_index(wiki_root: Path, g_meta: dict, cfg: dict, today: str) -> None:
    clusters = cfg.get("clusters") or {}
    tag_map = (cfg.get("tags") or {}).get("guideline") or {}
    by_tag: dict[str, list[tuple[str, dict]]] = {}
    for gid, info in g_meta.items():
        for tag in tag_map.get(gid) or ["untagged"]:
            by_tag.setdefault(tag, []).append((gid, info))

    cluster_for_id = {gid: slug for slug, c in clusters.items() for gid in (c or {}).get("members") or []}

    lines = [
        "---",
        "type: section-index",
        "section: guidelines",
        f"verified_at: {today}",
        f"count: {len(g_meta) + len(clusters)}",
        f"atomic: {len(g_meta)}",
        f"clusters: {len(clusters)}",
        "---",
        "",
        "# Guidelines",
        "",
        "Atomic, trigger-tagged lessons plus aggregator **cluster pages** that "
        "group related variants. Cluster pages have the suffix `__cluster.md` and "
        "are recall-preferred — when a cluster and its members both match a query, "
        "the cluster wins. Members carry a `superseded_by:` field pointing at "
        "their cluster.",
        "",
    ]
    if clusters:
        lines.append("## Clusters (prefer these first)")
        lines.append("")
        for slug, info in clusters.items():
            info = info or {}
            n = len((info.get("members") or []))
            tags = ", ".join(info.get("tags") or [])
            lines.append(f"- **[{info.get('title') or slug}]({slug}__cluster.md)** `cluster:{slug}` — `tags: {tags}` ({n} members)")
        lines.append("")
    lines.append("## Atomic guidelines, alphabetical")
    lines.append("")
    for gid, info in sorted(g_meta.items(), key=lambda x: x[1]["title"]):
        snippet = info["first_para"]
        if len(snippet) > 140:
            snippet = snippet[:140].rsplit(" ", 1)[0] + "…"
        cs = cluster_for_id.get(gid)
        clink = f" [→ cluster: {cs}]({cs}__cluster.md)" if cs else ""
        lines.append(f"- **[{info['title']}]({Path(info['relpath']).name})** `{gid}`{clink}")
        lines.append(f"  - {snippet}")
    if any(len(v) >= 2 for v in by_tag.values()):
        lines.append("")
        lines.append("## By tag")
        lines.append("")
        for tag in sorted(by_tag.keys()):
            members = by_tag[tag]
            if len(members) < 2:
                continue
            lines.append(f"### `{tag}`")
            lines.append("")
            for gid, info in sorted(members, key=lambda x: x[1]["title"]):
                lines.append(f"- [{info['title']}]({Path(info['relpath']).name}) `{gid}`")
            lines.append("")

    # Recall roll-up: per-guideline counts of how many summaries recalled
    # this rule, broken down by status. Always rendered when there is at
    # least one atomic guideline (zero-recall rows show all zeros so the
    # reader can see what's been contributed but not yet validated).
    if g_meta:
        usages = _scan_recalled_guidelines_in_summaries(wiki_root)
        lines.append("")
        lines.append("## Recall roll-up")
        lines.append("")
        lines.append(
            "Cross-summary tally of `recalled_guidelines:` blocks. "
            "Rows are alphabetical by guideline title. A row of zeros "
            "means the guideline has been contributed by a session "
            "but never recalled by another."
        )
        lines.append("")
        lines.append("| Guideline | Total | followed | ignored | contradicted | harmful |")
        lines.append("|-----------|------:|---------:|--------:|-------------:|--------:|")
        for gid, info in sorted(g_meta.items(), key=lambda x: x[1]["title"]):
            rows = usages.get(gid) or []
            counts = {s: 0 for s in ALLOWED_STATUSES}
            for r in rows:
                st = r["status"]
                if st in counts:
                    counts[st] += 1
            total = sum(counts.values())
            lines.append(
                f"| [{info['title']}]({Path(info['relpath']).name}) "
                f"| {total} | {counts['followed']} | {counts['ignored']} "
                f"| {counts['contradicted']} | {counts['harmful']} |"
            )
        lines.append("")

    # ── Pages by priority — unified table across clusters + atomics ──
    # Lives at the bottom of the page so it doesn't crowd the bullets above.
    if g_meta or clusters:
        usages_for_priority = _scan_recalled_guidelines_in_summaries(wiki_root)
        lines.extend(
            _render_priority_table(
                g_meta=g_meta,
                clusters=clusters,
                tag_map=tag_map,
                cluster_for_id=cluster_for_id,
                usages=usages_for_priority,
                today=today,
            )
        )

    (wiki_root / GUIDELINES_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_tasks_index(wiki_root: Path, cfg: dict, today: str) -> None:
    tasks = cfg.get("tasks") or {}
    tasks_dir = wiki_root / TASKS_DIR
    tasks_dir.mkdir(parents=True, exist_ok=True)
    subtasks = _scan_subtasks(tasks_dir)
    lines = [
        "---",
        "type: section-index",
        "section: tasks",
        f"verified_at: {today}",
        f"task_pages: {len(tasks)}",
        f"subtask_pages: {len(subtasks)}",
        "---",
        "",
        "# Tasks",
        "",
        "Two kinds of pages live here, distinguished by filename suffix:",
        "",
        "- **`__task.md`** — cross-session task-comparisons. Joins all sessions "
        "that attempted the same task across trials and conditions; defined in "
        "`_config.yaml` under `tasks:`.",
        "- **`__subtask.md`** — narrative slices within a single session. Authored standalone; not regenerated from config.",
        "",
    ]
    if tasks:
        lines.append("## Task comparisons")
        lines.append("")
        for slug, info in tasks.items():
            info = info or {}
            lines.append(f"- **[{info.get('title') or slug}]({slug}__task.md)** — `{info.get('family') or slug}` family")
        lines.append("")
    if subtasks:
        lines.append("## Subtasks (per-session workstreams)")
        lines.append("")
        # group by parent_session_id when present
        grouped: dict[str, list[dict]] = {}
        for st in subtasks:
            grouped.setdefault(st.get("parent_session_id") or "(unknown)", []).append(st)
        for sid, items in sorted(grouped.items()):
            head = sid[:12] + "…" if len(sid) > 12 else sid
            lines.append(f"### Session `{head}`")
            lines.append("")
            for st in sorted(items, key=lambda x: x.get("title") or x.get("slug") or ""):
                lines.append(f"- **[{st.get('title') or st.get('slug')}]({st['filename']})**")
            lines.append("")
    (tasks_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _scan_subtasks(tasks_dir: Path) -> list[dict]:
    out: list[dict] = []
    if not tasks_dir.is_dir():
        return out
    for p in sorted(tasks_dir.glob("*__subtask.md")):
        text = p.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        title_m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
        slug_m = re.search(r"^slug:\s*(.+)$", fm, re.MULTILINE)
        psid_m = re.search(r"^parent_session_id:\s*(.+)$", fm, re.MULTILINE)
        psum_m = re.search(r"^parent_summary:\s*(.+)$", fm, re.MULTILINE)
        tags_m = re.search(r"^tags:\s*\[(.*?)\]\s*$", fm, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else None
        if title and title.startswith('"') and title.endswith('"'):
            try:
                title = json.loads(title)
            except Exception:
                pass
        out.append(
            {
                "filename": p.name,
                "slug": slug_m.group(1).strip() if slug_m else p.stem.replace("__subtask", ""),
                "title": title or p.stem,
                "parent_session_id": (psid_m.group(1).strip() if psid_m else ""),
                "parent_summary": (psum_m.group(1).strip() if psum_m else ""),
                "tags": [x.strip() for x in (tags_m.group(1).split(",") if tags_m else []) if x.strip()],
            }
        )
    return out


def _scan_skills(wiki_root: Path) -> dict[str, dict]:
    """Return {slug: {path, relpath, name, description, trigger, related_summary,
    verified_at, tags}} for every wiki skill at <wiki>/skills/<slug>/SKILL.md.
    """
    out: dict[str, dict] = {}
    sk_dir = wiki_root / SKILLS_DIR
    if not sk_dir.is_dir():
        return out
    for sub in sorted(p for p in sk_dir.iterdir() if p.is_dir()):
        skill_md = sub / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        fm, body = split_frontmatter(text)
        if fm is None:
            continue
        try:
            data = yaml.safe_load(fm) or {}
        except yaml.YAMLError:
            continue
        slug = str(data.get("name") or sub.name).strip()
        out[slug] = {
            "path": skill_md,
            "relpath": f"{SKILLS_DIR}/{sub.name}/SKILL.md",
            "name": slug,
            "description": str(data.get("description") or "").strip(),
            "trigger": str(data.get("trigger") or "").strip(),
            "related_summary": str(data.get("related_summary") or "").strip(),
            "verified_at": str(data.get("verified_at") or "").strip(),
            "tags": data.get("tags") or [],
        }
    return out


def _write_skills_index(wiki_root: Path, skills: dict[str, dict], today: str) -> None:
    sk_dir = wiki_root / SKILLS_DIR
    sk_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "---",
        "type: section-index",
        "section: skills",
        f"verified_at: {today}",
        f"count: {len(skills)}",
        "---",
        "",
        "# Skills",
        "",
        "Wiki-resident, callable workflow pages. Each `<slug>/SKILL.md` is a "
        "structured procedural artifact: frontmatter + Overview + When To Use + "
        "Workflow + (optional) supporting scripts under `<slug>/scripts/`. At "
        "retrieval time, skills sort between clusters and atomic guidelines in "
        "`_index.jsonl` — directly callable, recall-preferred over guidelines "
        "for the same trigger.",
        "",
    ]
    if not skills:
        lines.append("_(none yet — synthesize one via `agent-wiki-synthesize-skill`)_")
        lines.append("")
    else:
        lines.append("| Skill | Description | Trigger | Verified at |")
        lines.append("|---|---|---|---|")
        for slug, info in sorted(skills.items()):
            trig = (info.get("trigger") or "—").replace("|", "\\|")
            if len(trig) > 80:
                trig = trig[:77] + "…"
            desc = (info.get("description") or "—").replace("|", "\\|")
            if len(desc) > 80:
                desc = desc[:77] + "…"
            lines.append(f"| **[{slug}]({slug}/SKILL.md)** | {desc} | {trig} | {info.get('verified_at') or today} |")
        lines.append("")
    (sk_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_jsonl_index(wiki_root: Path, cfg: dict, g_meta: dict) -> None:
    rows = []
    clusters = cfg.get("clusters") or {}
    tag_map = (cfg.get("tags") or {}).get("guideline") or {}
    cluster_for_id = {gid: slug for slug, c in clusters.items() for gid in (c or {}).get("members") or []}

    # clusters first
    for slug, info in clusters.items():
        info = info or {}
        rows.append(
            {
                "kind": "cluster",
                "id": f"cluster:{slug}",
                "title": info.get("title") or slug,
                "tags": info.get("tags") or [],
                "trigger": "",
                "summary": (info.get("description") or "")[:240],
                "link": f"{GUIDELINES_DIR}/{slug}__cluster.md",
                "members": info.get("members") or [],
                "priority": "high",
            }
        )

    # skills (recall-preferred over plain atomics)
    for slug, info in sorted(_scan_skills(wiki_root).items()):
        rows.append(
            {
                "kind": "skill",
                "id": f"skill:{slug}",
                "title": info["name"],
                "tags": info.get("tags") or [],
                "trigger": info.get("trigger") or "",
                "summary": (info.get("description") or "")[:240],
                "link": info["relpath"],
                "priority": "high",
            }
        )

    # atomic guidelines
    for gid, info in g_meta.items():
        snippet = info["first_para"]
        if len(snippet) > 240:
            snippet = snippet[:240].rsplit(" ", 1)[0] + "…"
        cs = cluster_for_id.get(gid)
        row = {
            "kind": "guideline",
            "id": gid,
            "title": info["title"],
            "tags": tag_map.get(gid) or [],
            "trigger": info["trigger"],
            "summary": snippet,
            "link": info["relpath"],
            "cluster": cs,
        }
        if cs:
            row["superseded_by"] = f"{cs}__cluster.md"
        rows.append(row)

    # tasks (cross-session comparison pages)
    for slug, info in (cfg.get("tasks") or {}).items():
        info = info or {}
        rows.append(
            {
                "kind": "task",
                "id": f"task:{slug}",
                "title": info.get("title") or slug,
                "tags": info.get("tags") or [],
                "trigger": "",
                "summary": (info.get("intro") or info.get("findings") or "")[:240],
                "link": f"{TASKS_DIR}/{slug}__task.md",
                "family": info.get("family") or slug,
            }
        )

    # subtasks (per-session workstreams)
    subtasks = _scan_subtasks(wiki_root / TASKS_DIR)
    for st in subtasks:
        rows.append(
            {
                "kind": "subtask",
                "id": f"subtask:{st['slug']}",
                "title": st["title"],
                "tags": st.get("tags") or [],
                "trigger": "",
                "summary": "",
                "link": f"{TASKS_DIR}/{st['filename']}",
                "parent_session_id": st.get("parent_session_id") or None,
                "parent_summary": st.get("parent_summary") or None,
            }
        )

    p = wiki_root / JSONL_INDEX_FILENAME
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build/refresh the wiki-twobatch wiki.")
    parser.add_argument("--wiki-root", type=Path, default=None, help="Override the wiki root directory.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sum = sub.add_parser("render-summary", help="stdin JSON -> summaries/<sid>.md")
    p_sum.add_argument("--rewrite", action="store_true")

    p_g = sub.add_parser("render-guidelines", help="stdin {entities: [...]} -> guideline pages")
    p_g.add_argument("--rewrite", action="store_true")
    p_g.add_argument("--session-id", default=None)
    p_g.add_argument("--normalized-path", default=None)

    p_cluster = sub.add_parser("render-cluster", help="stdin JSON -> guidelines/<slug>__cluster.md (also writes _config)")
    p_cluster.add_argument(
        "--archive-members",
        action="store_true",
        help="Move each member atomic to <wiki>/_archived/ after writing the cluster page (delete-on-promote).",
    )
    sub.add_parser("render-task", help="stdin JSON -> tasks/<slug>__task.md (also writes _config)")
    sub.add_parser("render-subtask", help="stdin JSON -> tasks/<slug>__subtask.md (per-session workstream page)")
    p_skill = sub.add_parser("render-skill", help="stdin JSON -> skills/<slug>/SKILL.md (+ scripts/)")
    p_skill.add_argument("--rewrite", action="store_true", help="Overwrite an existing skill page.")
    p_skill.add_argument(
        "--archive-covered",
        action="store_true",
        help="After writing the skill, archive any atomic guideline whose tags/title indicate it's covered by this skill.",
    )
    sub.add_parser("update-config", help="stdin patch -> _config.yaml")
    sub.add_parser("dump-guidelines", help="stdout: corpus of atomic guidelines as JSON")
    sub.add_parser("dump-summaries", help="stdout: corpus of summaries as JSON")
    sub.add_parser("catalog", help="refresh indexes, _index.jsonl, summary frontmatter metrics")

    args = parser.parse_args(argv)
    handlers = {
        "render-summary": cmd_render_summary,
        "render-guidelines": cmd_render_guidelines,
        "render-cluster": cmd_render_cluster,
        "render-task": cmd_render_task,
        "render-subtask": cmd_render_subtask,
        "render-skill": cmd_render_skill,
        "update-config": cmd_update_config,
        "dump-guidelines": cmd_dump_guidelines,
        "dump-summaries": cmd_dump_summaries,
        "catalog": cmd_catalog,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
