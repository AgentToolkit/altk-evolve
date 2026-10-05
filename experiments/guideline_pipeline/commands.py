"""CLI wiring for the stages that read a namespace: consolidate, export and lineage.

Kept out of ``__main__`` so that file only gains one call; each subcommand sets
``run``, which returns the exit code: 0 on success, 1 when writing the output
fails or --input can't be read, 2 for a usage error (unknown namespace or adapter, invalid thresholds,
unrecognized evidence).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.exceptions import NamespaceNotFoundException

from experiments.guideline_pipeline.adapters import get_adapter
from experiments.guideline_pipeline.guidelines import fetch_guidelines, guideline_rows
from experiments.guideline_pipeline.output import write_json_atomic
from experiments.guideline_pipeline.stages.consolidate import consolidate
from experiments.guideline_pipeline.stages.export import build_playbook, build_retrieval_index
from experiments.guideline_pipeline.stages.lineage import build_lineage, load_instructions

ClientFactory = Callable[[], EvolveClient]


def _support_threshold(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _instructions(args: argparse.Namespace) -> Mapping[str, str] | None:
    if (args.adapter is None) != (args.input is None):
        raise ValueError("--adapter and --input go together")
    if args.adapter is None:
        return None
    return load_instructions(get_adapter(args.adapter).records(args.input))


def _guarded(run: Callable[[argparse.Namespace, EvolveClient], str], make_client: ClientFactory) -> Callable[[argparse.Namespace], int]:
    def command(args: argparse.Namespace) -> int:
        try:
            summary = run(args, make_client())
        except (ValueError, NamespaceNotFoundException) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:  # reading --input or writing --out
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(summary)
        return 0

    return command


def _run_consolidate(args: argparse.Namespace, client: EvolveClient) -> str:
    return consolidate(client, args.namespace, threshold=args.threshold, mode=args.mode).summary()


def _run_playbook(args: argparse.Namespace, client: EvolveClient) -> str:
    min_support = args.min_support or client.config.min_support
    rows, empty = guideline_rows(fetch_guidelines(client, args.namespace))
    document, report = build_playbook(rows, min_support=min_support, empty=empty)
    write_json_atomic(args.out, document)
    return f"{report.summary()}; wrote {args.out}"


def _run_retrieval_index(args: argparse.Namespace, client: EvolveClient) -> str:
    instructions = _instructions(args)
    rows, empty = guideline_rows(fetch_guidelines(client, args.namespace))
    document, report = build_retrieval_index(
        rows,
        core_support=args.core_support or client.config.core_support,
        min_support=args.min_support or client.config.min_support,
        instructions=instructions,
        empty=empty,
    )
    write_json_atomic(args.out, document)
    for entity_id, reason in report.unresolved_ids:
        print(f"skipped {entity_id}: {reason}", file=sys.stderr)
    return f"{report.summary()}; wrote {args.out}"


def _run_lineage(args: argparse.Namespace, client: EvolveClient) -> str:
    instructions = _instructions(args)
    rows, empty = guideline_rows(fetch_guidelines(client, args.namespace))
    document, report = build_lineage(rows, args.namespace, instructions=instructions, empty=empty)
    write_json_atomic(args.out, document)
    return f"{report.summary()}; wrote {args.out}"


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--namespace", required=True, help="namespace to read guidelines from")
    parser.add_argument("--out", required=True, type=Path, help="output JSON path (written atomically)")


def _min_support(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--min-support", type=_support_threshold, help="drop guidelines with support below N (default: EVOLVE_MIN_SUPPORT, 1)"
    )


def _dataset(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter", help="dataset adapter the namespace was mined with; looks up source task instructions")
    parser.add_argument("--input", type=Path, help="dataset path for --adapter")


def add_output_stages(stages: Any, *, make_client: ClientFactory) -> None:
    """Register consolidate, export {playbook,retrieval-index} and lineage on the stage subparsers."""
    parser = stages.add_parser("consolidate", help="merge similar guidelines with Evolve's consolidation")
    parser.add_argument("--namespace", required=True, help="namespace to consolidate in place")
    parser.add_argument("--threshold", type=float, help="clustering cosine threshold (default: EVOLVE_CLUSTERING_THRESHOLD)")
    parser.add_argument(
        "--mode", choices=("none", "lossless", "lossy"), help="consolidation mode (default: EVOLVE_CONSOLIDATION_MODE, lossless)"
    )
    parser.set_defaults(run=_guarded(_run_consolidate, make_client))

    export = stages.add_parser("export", help="write a namespace as a playbook or retrieval index")
    targets = export.add_subparsers(dest="target", required=True)
    parser = targets.add_parser("playbook", help='{"entries": [{"r", "n", "e"}]}, support descending')
    _common(parser)
    _min_support(parser)
    parser.set_defaults(run=_guarded(_run_playbook, make_client))

    parser = targets.add_parser("retrieval-index", help='{"core": [...], "singletons": [{"rule", "source_task", "source_instruction"}]}')
    _common(parser)
    parser.add_argument(
        "--core-support", type=_support_threshold, help="support at or above which a guideline is core (default: EVOLVE_CORE_SUPPORT, 3)"
    )
    _min_support(parser)
    _dataset(parser)
    parser.set_defaults(run=_guarded(_run_retrieval_index, make_client))

    parser = stages.add_parser("lineage", help="each guideline's metadata.sources and source task ids, as JSON")
    _common(parser)
    _dataset(parser)
    parser.set_defaults(run=_guarded(_run_lineage, make_client))
