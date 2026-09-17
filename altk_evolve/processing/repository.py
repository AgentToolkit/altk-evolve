"""Versioned profile tables in the configured database, independent of application scope."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Protocol, TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    import psycopg

from altk_evolve.processing.models import ProfileConflict, ProfileNotFound


class ProfileRepository(Protocol):
    def get(self, name: str, revision: int | None = None) -> tuple[int, dict]: ...
    def put(self, name: str, value: dict, *, expected_revision: int) -> int: ...


class InMemoryProfileRepository:
    def __init__(self):
        self._profiles: dict[str, list[str]] = {}
        self._lock = Lock()

    def get(self, name, revision=None):
        with self._lock:
            versions = self._profiles.get(name, [])
            number = len(versions) if revision is None else revision
            if number < 1 or number > len(versions):
                raise ProfileNotFound(f"Profile not found: {name}@{number}")
            return number, json.loads(versions[number - 1])

    def put(self, name, value, *, expected_revision):
        encoded = json.dumps(value)
        with self._lock:
            versions = self._profiles.get(name, [])
            if len(versions) != expected_revision:
                raise ProfileConflict(f"Profile {name} changed; current revision is {len(versions)}")
            self._profiles[name] = [*versions, encoded]
            return len(versions) + 1


class SQLiteProfileRepository:
    """Immutable revision rows; expected_revision=0 is create-only.

    Connections are operation-local. BEGIN IMMEDIATE serializes conditional writes
    across instances/processes using the same SQLite file.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use InMemoryProfileRepository for in-memory storage")
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS processing_profiles "
                "(id TEXT NOT NULL, revision INTEGER NOT NULL, definition TEXT NOT NULL, "
                "PRIMARY KEY(id, revision))"
            )

    def get(self, name, revision=None):
        with sqlite3.connect(self.path) as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT revision, definition FROM processing_profiles WHERE id=? ORDER BY revision DESC LIMIT 1", (name,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT revision, definition FROM processing_profiles WHERE id=? AND revision=?", (name, revision)
                ).fetchone()
        if row is None:
            raise ProfileNotFound(f"Profile not found: {name}@{revision or 'latest'}")
        return row[0], json.loads(row[1])

    def put(self, name, value, *, expected_revision):
        encoded = json.dumps(value)
        with sqlite3.connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT COALESCE(MAX(revision), 0) FROM processing_profiles WHERE id=?", (name,)).fetchone()[0]
            if current != expected_revision:
                raise ProfileConflict(f"Profile {name} changed; current revision is {current}")
            revision = current + 1
            connection.execute("INSERT INTO processing_profiles VALUES (?, ?, ?)", (name, revision, encoded))
        return revision


class PostgresProfileRepository:
    """Profile revisions in the entity backend's PostgreSQL database.

    The backend supplies its configured connection factory. Operation-local
    connections let an admin publish while another connection processes a trajectory.
    """

    def __init__(self, connect: Callable[[], "psycopg.Connection"]):
        self._connect = connect
        with self._connect() as connection, connection.transaction():
            # CREATE TABLE IF NOT EXISTS alone can race on PostgreSQL's catalogs.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("evolve.processing_profiles.schema",))
            connection.execute(
                "CREATE TABLE IF NOT EXISTS processing_profiles "
                "(id TEXT NOT NULL, revision INTEGER NOT NULL, definition JSONB NOT NULL, "
                "PRIMARY KEY(id, revision))"
            )

    def get(self, name, revision=None):
        with self._connect() as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT revision, definition FROM processing_profiles WHERE id=%s ORDER BY revision DESC LIMIT 1", (name,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT revision, definition FROM processing_profiles WHERE id=%s AND revision=%s", (name, revision)
                ).fetchone()
        if row is None:
            raise ProfileNotFound(f"Profile not found: {name}@{revision or 'latest'}")
        return row[0], row[1]

    def put(self, name, value, *, expected_revision):
        from psycopg.types.json import Jsonb

        with self._connect() as connection, connection.transaction():
            # Serialize writers for this profile, including its first revision.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("evolve.processing_profiles:" + name,))
            current = connection.execute("SELECT COALESCE(MAX(revision), 0) FROM processing_profiles WHERE id=%s", (name,)).fetchone()[0]
            if current != expected_revision:
                raise ProfileConflict(f"Profile {name} changed; current revision is {current}")
            revision = current + 1
            connection.execute(
                "INSERT INTO processing_profiles (id, revision, definition) VALUES (%s, %s, %s)", (name, revision, Jsonb(value))
            )
        return revision
