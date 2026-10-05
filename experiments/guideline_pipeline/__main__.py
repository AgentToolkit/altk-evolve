"""Run a guideline pipeline stage against the configured Evolve backend."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from altk_evolve.frontend.client.evolve_client import EvolveClient

from experiments.guideline_pipeline.adapters import get_adapter
from experiments.guideline_pipeline.runtime import stabilize_runtime
from experiments.guideline_pipeline.stages.mine import mine


def make_client() -> EvolveClient:
    """A client for the configured backend (EVOLVE_* environment / .env)."""
    stabilize_runtime()
    return EvolveClient()


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m experiments.guideline_pipeline", description=__doc__)
    stages = parser.add_subparsers(dest="stage", required=True)
    mine_parser = stages.add_parser("mine", help="generate guidelines from a dataset through a processing profile")
    mine_parser.add_argument("--adapter", required=True, help="registered dataset adapter name")
    mine_parser.add_argument("--input", required=True, type=Path, help="dataset path, as the adapter expects it")
    mine_parser.add_argument("--namespace", required=True, help="namespace to write guidelines into (created if missing)")
    mine_parser.add_argument("--processing-profile", required=True, help="published processing profile id")
    mine_parser.add_argument("--revision", type=_positive, help="pin a profile revision (default: latest, resolved once per run)")
    mine_parser.add_argument("--limit", type=_positive, help="process at most N records")
    mine_parser.add_argument("--dry-run", action="store_true", help="build and validate trajectories without touching storage")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        adapter = get_adapter(args.adapter)
        report = mine(
            adapter.records(args.input),
            None if args.dry_run else make_client(),
            namespace_id=args.namespace,
            processing_profile=args.processing_profile,
            revision=args.revision,
            limit=args.limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for trace_id, error in report.failures:
        print(f"failed {trace_id}: {error}", file=sys.stderr)
    print(report.summary())
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
