"""Convert one file into a trajectory with a built-in converter.

Usage:
  uv run python -m altk_forge.cli.convert langfuse <trace.langfuse.json> <out.trajectory.json> [--host URL]
  uv run python -m altk_forge.cli.convert messages <flat.json> <out.trajectory.json> [--agent-id ID]

langfuse: a Langfuse trace export (GET /api/public/traces/{id}) of a LangGraph agent.
messages: a flat 1.0 trajectory (OpenAI `messages` of a single agent, e.g. from MCP save_trajectory).
"""

import argparse
import json
from pathlib import Path

from ..builtin import langfuse, messages


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    kind = ap.add_subparsers(dest="kind", required=True)
    lf = kind.add_parser("langfuse", help="a Langfuse trace export of a LangGraph agent")
    lf.add_argument("trace")
    lf.add_argument("out")
    lf.add_argument("--host", default=None, help="Langfuse host, used for source.uri")
    ms = kind.add_parser("messages", help="a flat 1.0 trajectory (OpenAI messages)")
    ms.add_argument("flat")
    ms.add_argument("out")
    ms.add_argument("--agent-id")
    args = ap.parse_args()
    if args.kind == "langfuse":
        doc = langfuse.convert(json.loads(Path(args.trace).read_text()), args.host)
    else:
        doc = messages.convert(json.loads(Path(args.flat).read_text()), args.agent_id)
    Path(args.out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
