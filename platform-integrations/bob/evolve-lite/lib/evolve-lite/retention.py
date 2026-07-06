"""Data retention for the evolve-lite plugin store (issue #275).

Mirrors the ``altk_evolve/retention/`` semantics against the plugin's
``.evolve/`` file store: age-based and unused-based rules that **flag** or
**delete**, dry-run by default, and the session -> derived cascade. Stdlib-only,
because plugin scripts run in the host's Python where nothing beyond the
standard library is guaranteed.

The plugin store is markdown files, not a backend with metadata, so the
package's signals map onto what the files actually carry:

- **age** — file mtime. Plugin entities have no ``created_at`` frontmatter;
  mtime is the best available signal (flagging preserves it, see below).
- **unused** — the latest ``recall`` row in ``.evolve/audit.log`` naming the
  entity id (``<type>/<name>``, the id scheme ``audit_recall.py`` logs),
  falling back to file mtime when an entity was never recalled.
- **cascade** — the package links a derived memory to its session via
  ``metadata.source_task_id == trace_id``. Plugin-side, the learn skill stamps
  each saved entity with a ``trajectory:`` frontmatter path pointing at the
  session file in ``.evolve/trajectories/``. Deleting a trajectory file under a
  ``cascade_derived`` rule also deletes entities whose link resolves to it
  (matched by filename — trajectory filenames embed a timestamp + session id
  and are unique by construction).

Actions:

- **flag** — upsert ``retention_flagged_at`` / ``retention_reason`` /
  ``retention_rule`` into the entity's frontmatter (non-destructive; the file's
  mtime is preserved so the age clock does not reset). Trajectory files are
  opaque JSON with no frontmatter, so their flag lives in the audit log only.
- **delete** — unlink the file.

Every applied (non-dry-run) action also appends an ``event: "retention"`` row
to ``.evolve/audit.log`` for auditability.

Scope: private entities under ``.evolve/entities/`` — excluding
``entities/subscribed/`` (git clones managed by the sync skill; local deletes
there would be clobbered by the next sync) — plus session files under
``.evolve/trajectories/``.

Rules load from a ``retention:`` block in ``evolve.config.yaml``::

    retention:
      rules:
        - name: stale-guidelines
          entity_type: guideline
          max_age_days: 90
          action: flag
        - name: old-sessions
          entity_type: trajectory
          max_age_days: 365
          action: delete
          cascade_derived: true
"""

import datetime
import json
import os
from pathlib import Path

VALID_ACTIONS = ("flag", "delete")

#: How the trajectory tree is typed in rules (matches the package's
#: ``entity_type: trajectory`` convention).
TRAJECTORY_TYPE = "trajectory"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def validate_rules(raw_rules):
    """Validate a raw ``rules`` list into normalized rule dicts.

    Mirrors the package's ``RetentionRule`` schema: every rule needs a ``name``
    and at least one of ``max_age_days`` / ``max_unused_days``; ``action``
    defaults to ``flag``. Raises ``ValueError`` on the first invalid rule.
    """
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, list):
        raise ValueError("retention rules must be a list")
    rules = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError(f"retention rule must be a mapping, got {type(raw).__name__}")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("retention rule must have a non-empty string 'name'")
        rule = {
            "name": name.strip(),
            "entity_type": None,
            "max_age_days": _coerce_days(raw.get("max_age_days"), name, "max_age_days"),
            "max_unused_days": _coerce_days(raw.get("max_unused_days"), name, "max_unused_days"),
            "action": raw.get("action", "flag"),
            "cascade_derived": bool(raw.get("cascade_derived", False)),
        }
        entity_type = raw.get("entity_type")
        if entity_type is not None:
            if not isinstance(entity_type, str) or not entity_type.strip():
                raise ValueError(f"retention rule {name!r}: entity_type must be a non-empty string")
            rule["entity_type"] = entity_type.strip()
        if rule["max_age_days"] is None and rule["max_unused_days"] is None:
            raise ValueError(f"retention rule {name!r} must set max_age_days and/or max_unused_days")
        if rule["action"] not in VALID_ACTIONS:
            raise ValueError(f"retention rule {name!r}: action must be one of {VALID_ACTIONS}, got {rule['action']!r}")
        rules.append(rule)
    return rules


def _coerce_days(value, rule_name, key):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"retention rule {rule_name!r}: {key} must be a number, got {value!r}")
    if value < 0:
        raise ValueError(f"retention rule {rule_name!r}: {key} must be >= 0")
    return float(value)


def load_rules(config):
    """Return validated rules from an evolve config dict's ``retention:`` block.

    Returns ``[]`` when no block / no rules are configured.
    """
    retention_cfg = (config or {}).get("retention") or {}
    if not isinstance(retention_cfg, dict):
        raise ValueError("'retention' config block must be a mapping")
    return validate_rules(retention_cfg.get("rules"))


def load_policy_file(path):
    """Load rules from a standalone policy file (JSON, or the same minimal
    YAML subset ``evolve.config.yaml`` uses). The file holds a top-level
    ``rules:`` list, like the package's ``retention.example.yaml``."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        from config import _parse_yaml  # sibling lib module; resolved via sys.path

        data = _parse_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"policy file {path} must hold a mapping with a 'rules' list")
    return validate_rules(data.get("rules"))


# ---------------------------------------------------------------------------
# Store scan
# ---------------------------------------------------------------------------


def _utc(dt):
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _parse_iso(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return _utc(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _mtime(path):
    return datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)


def _frontmatter(path):
    """Parse simple ``key: value`` frontmatter lines from a markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key and value:
            out[key] = value
    return {}


def scan_store(evolve_dir):
    """Return one item dict per retention-eligible file in the store.

    Items are shaped ``{id, type, path, mtime, trajectory_link}`` where
    ``trajectory_link`` is the filename of the session the entity was derived
    from (via its ``trajectory:`` frontmatter), or ``None``.
    """
    evolve_dir = Path(evolve_dir)
    items = []

    entities_root = evolve_dir / "entities"
    if entities_root.is_dir():
        for md in sorted(entities_root.glob("**/*.md")):
            rel = md.relative_to(entities_root)
            # subscribed/ trees are git clones owned by the sync skill; local
            # deletes there would be silently restored by the next sync.
            if rel.parts and rel.parts[0] == "subscribed":
                continue
            if md.is_symlink() or ".git" in rel.parts:
                continue
            meta = _frontmatter(md)
            link = meta.get("trajectory")
            items.append(
                {
                    "id": rel.with_suffix("").as_posix(),
                    "type": meta.get("type") or (rel.parts[0] if len(rel.parts) > 1 else "guideline"),
                    "path": md,
                    "mtime": _mtime(md),
                    "trajectory_link": Path(link).name if link else None,
                }
            )

    traj_root = evolve_dir / "trajectories"
    if traj_root.is_dir():
        for traj in sorted(traj_root.iterdir()):
            if not traj.is_file() or traj.is_symlink():
                continue
            items.append(
                {
                    "id": f"trajectories/{traj.name}",
                    "type": TRAJECTORY_TYPE,
                    "path": traj,
                    "mtime": _mtime(traj),
                    "trajectory_link": None,
                }
            )

    return items


def last_access_index(evolve_dir):
    """Map entity id -> latest ``recall`` timestamp from ``.evolve/audit.log``.

    This is the plugin's "unused" signal: ``audit_recall.py`` appends a recall
    row (with ``entities: ["<type>/<name>", ...]``) every time memories are
    consulted, mirroring what ``EvolveClient.record_access`` stamps package-side.
    """
    audit_log = Path(evolve_dir) / "audit.log"
    index = {}
    if not audit_log.is_file():
        return index
    for line in audit_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("event") != "recall":
            continue
        ts = _parse_iso(row.get("ts"))
        if ts is None:
            continue
        for eid in row.get("entities") or []:
            if isinstance(eid, str) and eid:
                prev = index.get(eid)
                if prev is None or ts > prev:
                    index[eid] = ts
    return index


# ---------------------------------------------------------------------------
# Evaluation (mirrors altk_evolve.retention.engine.RetentionEngine.evaluate)
# ---------------------------------------------------------------------------


def _match(item, rule, now, last_access):
    """Return the trigger reason ('age'|'unused') if *rule* matches, else None."""
    if rule["entity_type"] is not None and item["type"] != rule["entity_type"]:
        return None
    age_days = (now - item["mtime"]).total_seconds() / 86400.0
    if rule["max_age_days"] is not None and age_days > rule["max_age_days"]:
        return "age"
    if rule["max_unused_days"] is not None:
        last = last_access.get(item["id"]) or item["mtime"]
        unused_days = (now - last).total_seconds() / 86400.0
        if unused_days > rule["max_unused_days"]:
            return "unused"
    return None


def evaluate(evolve_dir, rules, now=None):
    """Compute the actions the rules imply, without mutating anything.

    Returns a list of ``{id, type, action, reason, rule, path}`` dicts.
    First matching rule wins per item; delete supersedes flag; a delete rule
    with ``cascade_derived`` on a trajectory also deletes the entities whose
    ``trajectory:`` frontmatter links back to it.
    """
    now = _utc(now) if now else datetime.datetime.now(datetime.timezone.utc)
    items = scan_store(evolve_dir)
    last_access = last_access_index(evolve_dir)

    # Provenance index: trajectory filename -> [derived items]
    derived_by_name = {}
    for item in items:
        link = item["trajectory_link"]
        if link:
            derived_by_name.setdefault(link, []).append(item)

    # delete supersedes flag for the same item; first writer otherwise wins.
    actions = {}

    def record(item, action, reason, rule_name):
        existing = actions.get(item["id"])
        if existing is not None and (existing["action"] == "delete" or action == "flag"):
            return
        actions[item["id"]] = {
            "id": item["id"],
            "type": item["type"],
            "action": action,
            "reason": reason,
            "rule": rule_name,
            "path": item["path"],
        }

    for item in items:
        matched = None
        for rule in rules:
            reason = _match(item, rule, now, last_access)
            if reason is not None:
                matched = (rule, reason)
                break
        if matched is None:
            continue
        rule, reason = matched
        record(item, rule["action"], reason, rule["name"])

        if rule["action"] == "delete" and rule["cascade_derived"] and item["type"] == TRAJECTORY_TYPE:
            for derived in derived_by_name.get(item["path"].name, []):
                if derived["id"] == item["id"]:
                    continue
                record(derived, "delete", f"cascade:{item['path'].name}", rule["name"])

    return list(actions.values())


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def _flag_entity_file(path, flagged_at, reason, rule_name):
    """Upsert retention_* markers into an entity file's frontmatter.

    Preserves the file's mtime so flagging does not reset the age clock.
    """
    path = Path(path)
    st = path.stat()
    text = path.read_text(encoding="utf-8")
    marker = [
        f"retention_flagged_at: {flagged_at}",
        f"retention_reason: {reason}",
        f"retention_rule: {rule_name}",
    ]
    lines = text.splitlines()
    closing = None
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing = i
                break
    if closing is not None:
        head = [ln for ln in lines[1:closing] if not ln.strip().startswith("retention_")]
        new_lines = [lines[0], *head, *marker, *lines[closing:]]
    else:
        # No frontmatter (shouldn't happen for entities we wrote) — prepend one.
        new_lines = ["---", *marker, "---", "", *lines]
    new_text = "\n".join(new_lines)
    if text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")
    os.utime(path, (st.st_atime, st.st_mtime))


def apply_actions(evolve_dir, actions, dry_run=True, now=None):
    """Apply (or, on dry run, merely report) a list of evaluated actions.

    Returns ``{dry_run, flagged, deleted, errors}`` where flagged/deleted hold
    the action dicts and errors hold strings. Non-dry-run actions each append
    an ``event: "retention"`` row to the audit log.
    """
    now = _utc(now) if now else datetime.datetime.now(datetime.timezone.utc)
    flagged_at = now.isoformat()
    report = {"dry_run": dry_run, "flagged": [], "deleted": [], "errors": []}

    for action in actions:
        try:
            if action["action"] == "delete":
                if not dry_run:
                    Path(action["path"]).unlink(missing_ok=True)
                    _audit(evolve_dir, action)
                report["deleted"].append(action)
            else:  # flag
                if not dry_run:
                    if action["type"] != TRAJECTORY_TYPE:
                        _flag_entity_file(action["path"], flagged_at, action["reason"], action["rule"])
                    # Trajectory files are opaque JSON — no frontmatter to mark;
                    # the audit row below is their durable flag record.
                    _audit(evolve_dir, action)
                report["flagged"].append(action)
        except OSError as exc:  # don't let one bad file abort the sweep
            report["errors"].append(f"{action['action']} {action['id']}: {exc}")

    return report


def _audit(evolve_dir, action):
    import audit  # sibling lib module; resolved via sys.path

    audit.append(
        evolve_dir=str(evolve_dir),
        event="retention",
        action=action["action"],
        entity=action["id"],
        reason=action["reason"],
        rule=action["rule"],
    )


def run(evolve_dir, rules, dry_run=True, now=None):
    """Evaluate the rules against the store and apply (or dry-run) the result."""
    actions = evaluate(evolve_dir, rules, now=now)
    return apply_actions(evolve_dir, actions, dry_run=dry_run, now=now)


def summary(report):
    verb = "would flag/delete" if report["dry_run"] else "flagged/deleted"
    return f"{verb}: {len(report['flagged'])} flagged, {len(report['deleted'])} deleted, {len(report['errors'])} errors"


if __name__ == "__main__":
    # Self-test (pure paths only; file behavior is covered by pytest).
    assert load_rules({}) == []
    assert load_rules({"retention": {"rules": []}}) == []

    rules = validate_rules([{"name": "r", "max_age_days": 30}])
    assert rules[0]["action"] == "flag"  # default
    assert rules[0]["max_age_days"] == 30.0

    for bad in (
        [{"name": "r"}],  # no threshold
        [{"name": "r", "max_age_days": 30, "action": "purge"}],  # bad action
        [{"max_age_days": 30}],  # no name
        [{"name": "r", "max_age_days": "soon"}],  # non-numeric
    ):
        try:
            validate_rules(bad)
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expected ValueError for {bad}")

    assert _parse_iso("2026-06-29T00:00:00Z") is not None
    assert _parse_iso("not-a-date") is None
    assert _parse_iso(None) is None
    print("retention.py ok")
