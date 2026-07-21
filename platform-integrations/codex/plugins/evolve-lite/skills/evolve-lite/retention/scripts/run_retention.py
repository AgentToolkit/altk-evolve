#!/usr/bin/env python3
"""
Retention Script
Applies data-retention rules to the local .evolve store: flags or deletes
stale / unused memories and expired sessions. Dry-run by default; nothing
is mutated unless --apply is passed.
"""

import argparse
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple
# plugins can coexist side by side (e.g. .bob/lib/evolve-lite/).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import get_evolve_dir, log as _log  # noqa: E402
from config import load_config  # noqa: E402
import retention  # noqa: E402


def log(message):
    _log("retention", message)


def main():
    parser = argparse.ArgumentParser(description="Apply retention rules to the .evolve store (dry-run by default).")
    parser.add_argument("--apply", action="store_true", help="Actually flag/delete. Without this flag, only report.")
    parser.add_argument("--policy", default=None, help="Standalone policy file (JSON or YAML 'rules:' list) overriding evolve.config.yaml.")
    args = parser.parse_args()

    try:
        if args.policy:
            rules = retention.load_policy_file(args.policy)
        else:
            rules = retention.load_rules(load_config())
    except (ValueError, OSError) as exc:
        log(f"Invalid retention rules: {exc}")
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not rules:
        print(
            "No retention rules configured. Add a `retention:` block with a `rules:` list to evolve.config.yaml (or pass --policy <file>)."
        )
        sys.exit(0)

    evolve_dir = get_evolve_dir()
    if not evolve_dir.is_dir():
        print(f"No evolve store at {evolve_dir}; nothing to do.")
        sys.exit(0)
    evolve_dir = evolve_dir.resolve()

    dry_run = not args.apply
    report = retention.run(evolve_dir, rules, dry_run=dry_run)
    log(f"{'DRY RUN' if dry_run else 'APPLY'}: {retention.summary(report)}")

    if dry_run:
        print("DRY RUN — nothing was changed. Re-run with --apply to enforce.")
    else:
        print("APPLIED:")

    for action in [*report["deleted"], *report["flagged"]]:
        verb = action["action"].upper()
        print(f"  {verb:6} {action['id']}  reason={action['reason']}  rule={action['rule']}")
        print(f"         why: {action['detail']}")
    if not report["deleted"] and not report["flagged"]:
        print("  (no memories matched any rule)")

    for action in report.get("skipped", []):
        print(f"  SKIP   {action['id']}  reason={action['reason']}  rule={action['rule']}")
        print(f"         why: {action['detail']}")

    for warning in report["warnings"]:
        print(f"  WARNING  {warning}")
    for err in report["errors"]:
        print(f"  ERROR  {err}", file=sys.stderr)

    print(retention.summary(report))
    sys.exit(1 if report["errors"] else 0)


if __name__ == "__main__":
    main()
