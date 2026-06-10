#!/usr/bin/env python3
"""Append a recall-audit row to .evolve/audit.log.

Self-contained (no third-party or evolve-lite lib imports) so it can be dropped
at a single path and run by a model-invoked shell command on any platform.

Usage:
    python3 audit_recall.py <memory_file> [<memory_file> ...]

Records which memory entries the model consulted this turn so the `provenance`
analysis can later judge whether they influenced the outcome. Session id is
resolved from the host's environment when available and falls back to a freshly
minted UUID (printed as `evolve-session: <id>` for the model to echo).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _evolve_dir() -> Path:
    env = os.environ.get("EVOLVE_DIR")
    return Path(env) if env else Path.cwd() / ".evolve"


def _bob_session_id() -> str | None:
    """Recover Bob's real session id for the current run.

    Bob (a Gemini-CLI fork) exposes no session-id environment variable to tool
    subprocesses, but it writes the live session to
    ``~/.bob/tmp/<sha256(cwd)>/chats/session-<ts>-<sid8>.json`` with a real
    ``sessionId`` field (the filename's trailing segment is that id's first
    block). Recovering it lets `provenance` tie this recall to the saved
    trajectory instead of an opaque minted uuid. Gated on ``BOBSHELL_CLI`` so it
    is inert on every other host. Returns the id, or ``None`` when not under Bob
    or no chat file is found (caller then mints a uuid)."""
    if not os.environ.get("BOBSHELL_CLI"):
        return None
    try:
        # Bob hashes the project path it was launched in. os.getcwd() returns
        # the resolved (symlink-free) path, but Bob may have captured the
        # symlinked path the user cd'd through; $PWD preserves that. Try both
        # candidate hashes and pick the newest chat across them.
        chats = []
        seen_paths: set[str] = set()
        for raw in (os.getcwd(), os.environ.get("PWD")):
            if not raw or raw in seen_paths:
                continue
            seen_paths.add(raw)
            project_hash = hashlib.sha256(raw.encode()).hexdigest()
            chats.extend((Path.home() / ".bob" / "tmp" / project_hash / "chats").glob("session-*.json"))
        for chat in sorted(chats, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                sid = json.loads(chat.read_text(encoding="utf-8")).get("sessionId")
            except (OSError, json.JSONDecodeError):
                continue
            if sid:
                return str(sid)
    except OSError:
        return None
    return None


def _session_id() -> tuple[str, bool]:
    """Return (session_id, self_minted)."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        val = os.environ.get(var)
        if val:
            return val, False
    bob_sid = _bob_session_id()
    if bob_sid:
        return bob_sid, False
    return str(uuid.uuid4()), True


def main(argv: list[str]) -> int:
    entities = [a for a in argv if a.strip()]
    if not entities:
        return 0

    session_id, minted = _session_id()
    row = {
        "event": "recall",
        "session_id": session_id,
        "entities": entities,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }

    log = _evolve_dir() / "audit.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    if minted:
        print(f"evolve-session: {session_id}")
    count = len(entities)
    print(f"Recorded recall of {count} memory entr{'y' if count == 1 else 'ies'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
