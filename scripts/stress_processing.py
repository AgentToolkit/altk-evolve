"""Opt-in storage stress probe; no LLM calls or production data.

Run from the checkout with ``uv run python -m scripts.stress_processing --help``.
Embeddings and the processor are deterministic. Initial fixtures are bulk-loaded
through native storage to avoid measuring ingestion; measured operations use Evolve.
Postgres requires a disposable database via EVOLVE_STRESS_POSTGRES_DSN. All created
namespaces are removed; local databases live in a temporary directory. JSON lines
report measurements and correctness checks, including failures, without hiding them.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import resource
import statistics
import tempfile
from threading import Barrier
import time
import uuid
from unittest.mock import patch

import numpy as np
from pydantic import BaseModel


def emit(event, **data):
    print(json.dumps({"event": event, **data}), flush=True)


class Embeddings:
    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, content):
        vector = np.zeros(384, dtype=np.float32)
        vector[1 if "rare" in content else 0] = 1
        return vector


class Config(BaseModel):
    pass


class MemoryProcessor:
    id = "stress.memory"
    api_version = 1
    version = "1"
    config_model = Config

    @classmethod
    def from_config(cls, config):
        return cls()

    def process(self, trajectory, *, context):
        from altk_evolve.processing import ProcessorResult
        from altk_evolve.schema.core import Entity

        return ProcessorResult(entities=[Entity(type="note", content=trajectory.messages[0]["content"], metadata={"stress": True})])


def seed(client, ns, count):
    backend = client.backend
    name = type(backend).__name__
    if name.startswith("Filesystem"):
        data = backend._load_namespace_data(ns)
        now = datetime.now(UTC).isoformat()
        data.entities = [
            {"id": str(i), "type": "note", "content": f"memory {i}", "created_at": now, "metadata": {"position": i}}
            for i in range(1, count + 1)
        ]
        data.next_id = count + 1
        backend._save_namespace_data(ns, data)
    elif name.startswith("Postgres"):
        from psycopg import sql

        vector = str(Embeddings().encode("ordinary").tolist())
        backend.conn.execute(
            sql.SQL(
                "INSERT INTO {} (type,content,created_at,embedding,metadata) SELECT 'note', 'memory ' || i, 1, %s::vector, jsonb_build_object('position',i) FROM generate_series(1,%s) i"
            ).format(sql.Identifier(backend._table_name(ns))),
            (vector, count),
        )
        # Bulk fixture loading bypasses ordinary ingestion/autovacuum. Give the
        # planner current statistics before measuring selective indexed queries.
        backend.conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(backend._table_name(ns))))
    else:
        vector = Embeddings().encode("ordinary")
        for start in range(1, count + 1, 1000):
            backend.milvus.insert(
                ns,
                [
                    {"type": "note", "content": f"memory {i}", "created_at": 1, "embedding": vector, "metadata": {"position": i}}
                    for i in range(start, min(start + 1000, count + 1))
                ],
            )
        backend._post_update(ns)


def concurrent(label, users, operation):
    gate = Barrier(users)

    def run(user):
        gate.wait(timeout=60)
        start = time.perf_counter()
        try:
            value = operation(user)
            return {"user": user, "seconds": time.perf_counter() - start, "value": value}
        except Exception as exc:
            return {"user": user, "seconds": time.perf_counter() - start, "error": f"{type(exc).__name__}: {exc}"}

    start = time.perf_counter()
    with ThreadPoolExecutor(users) as pool:
        results = list(pool.map(run, range(users)))
    durations = sorted(r["seconds"] for r in results)
    emit(
        label,
        wall_seconds=time.perf_counter() - start,
        p50_seconds=statistics.median(durations),
        p95_seconds=durations[int(0.95 * (len(durations) - 1))],
        max_seconds=max(durations),
        results=results,
    )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["filesystem", "postgres", "milvus"], required=True)
    parser.add_argument("--size", type=int, default=1000, help="Initial entities PER namespace")
    parser.add_argument("--users", type=int, default=16)
    parser.add_argument("--isolated", action="store_true", help="One namespace per user; otherwise shared contention test")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--policy-probe", action="store_true", help="On Milvus, also exercise the shipped legal-hold hook (requires hooks extra)"
    )
    parser.add_argument("--reconcile", action="store_true", help="Force overlapping prepared replacements of the same entity")
    parser.add_argument("--checkpoints", type=int, default=0, help="Historical checkpoint records PER namespace")
    args = parser.parse_args()
    emit("case", **vars(args))
    original = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="evolve-stress-") as tmp, ExitStack() as stack:
        os.chdir(tmp)  # Avoid auto-discovered user hooks / local configuration.
        stack.callback(os.chdir, original)
        stack.enter_context(patch.dict(os.environ, {"EVOLVE_SQLITE_PATH": str(Path(tmp) / "metadata.db"), "EVOLVE_HOOKS_CONFIG": ""}))
        from altk_evolve.config.evolve import EvolveConfig
        from altk_evolve.frontend.client.evolve_client import EvolveClient
        from altk_evolve.schema.core import Entity

        if args.backend == "filesystem":
            from altk_evolve.config.filesystem import FilesystemSettings

            settings = FilesystemSettings(data_dir=str(Path(tmp) / "entities"), _env_file=None)
        elif args.backend == "postgres":
            from psycopg.conninfo import conninfo_to_dict
            from altk_evolve.config.postgres import PostgresDBSettings

            settings = PostgresDBSettings(**conninfo_to_dict(os.environ["EVOLVE_STRESS_POSTGRES_DSN"]), _env_file=None)
            stack.enter_context(patch("altk_evolve.backend.postgres.SentenceTransformer", lambda _: Embeddings()))
        else:
            from altk_evolve.config.milvus import MilvusDBSettings

            settings = MilvusDBSettings(uri=str(Path(tmp) / "milvus.db"), sqlite_uri=str(Path(tmp) / "metadata.db"), _env_file=None)
            stack.enter_context(patch("altk_evolve.backend.milvus.SentenceTransformer", lambda _: Embeddings()))
        logging.getLogger().setLevel(logging.ERROR)
        clients = [EvolveClient(EvolveConfig(backend=args.backend, settings=settings)) for _ in range(args.users)]
        for client in clients:
            stack.callback(client.backend.close)
        namespaces = ["stress_" + uuid.uuid4().hex for _ in range(args.users if args.isolated else 1)]
        start = time.perf_counter()
        for ns in namespaces:
            clients[0].create_namespace(ns)
            stack.callback(clients[0].delete_namespace, ns)
            seed(clients[0], ns, args.size)
            if args.checkpoints:
                backend = clients[0].backend
                if args.backend == "filesystem":
                    data = backend._load_namespace_data(ns)
                    data.processing_checkpoints = {
                        f"historical-{i}": {
                            "operation_id": str(i),
                            "processor_id": "memory",
                            "manifest": {"processors": [{"plugin": "stress.memory", "config": {}}]},
                        }
                        for i in range(args.checkpoints)
                    }
                    backend._save_namespace_data(ns, data)
                elif args.backend == "postgres":
                    backend.profile_repository()
                    backend.conn.execute(
                        "INSERT INTO processing_checkpoints (namespace_id,key,value) SELECT %s, 'historical-' || i, jsonb_build_object('operation_id',i,'processor_id','memory','manifest',jsonb_build_object('processors',jsonb_build_array(jsonb_build_object('plugin','stress.memory','config','{}'::jsonb)))) FROM generate_series(1,%s) i",
                        (ns, args.checkpoints),
                    )
        emit("seed", total=args.size * len(namespaces), seconds=time.perf_counter() - start)

        def namespace(user):
            return namespaces[user] if args.isolated else namespaces[0]

        # Known matching metadata is beyond the first 1,000 ordinary records.
        for ns in namespaces:
            clients[0].update_entities(ns, [Entity(type="note", content="rare target", metadata={"needle": True})], False)

        def lookup(user):
            backend = clients[user].backend
            ns = namespace(user)
            by_filter = backend.search_entities(ns, filters={"metadata.needle": True}, limit=1)
            by_vector_filter = backend.search_entities(ns, query="ordinary", filters={"metadata.needle": True}, limit=1)
            target = backend.search_entities(ns, query="rare target", limit=1)
            by_id = backend.scan_entities(ns, filters={"id": target[0].id}, limit=1) if target else []
            return {
                "metadata_found": len(by_filter),
                "vector_metadata_found": len(by_vector_filter),
                "exact_id_found": len(by_id),
                "target_correct": bool(target and target[0].content == "rare target"),
            }

        concurrent("lookup", args.users, lookup)
        if args.backend == "milvus":
            backend = clients[0].backend
            ns = namespaces[0]
            native = backend.milvus.query(ns, filter='metadata["needle"] == true', output_fields=["id", "metadata"], limit=1)
            exact = {
                kind: len(backend.scan_entities(ns, filters={"id": value}, limit=1))
                for kind, value in [("integer", native[0]["id"]), ("string", str(native[0]["id"]))]
            }
            expanded = backend.search_entities(ns, filters={"metadata.needle": True}, limit=args.size + 1) if args.size < 16000 else None
            emit(
                "milvus_filter_diagnostic",
                native_found=len(native),
                expanded_limit_found=len(expanded) if expanded is not None else None,
                exact_id=exact,
            )
            entity_id = native[0]["id"]
            for expression in [
                f"id == {entity_id}",
                f"id > 0 AND id == {entity_id}",
                f"id > 0 and id == {entity_id}",
                f"id > 0 && id == {entity_id}",
                f'id == "{entity_id}"',
            ]:
                try:
                    rows = backend.milvus.query(ns, filter=expression, output_fields=["id"], limit=1000)
                    emit("native_filter_expression", expression=expression, found=len(rows))
                except Exception as exc:
                    emit("native_filter_expression", expression=expression, error=f"{type(exc).__name__}: {exc}")
        target_ids = [clients[0].backend.search_entities(ns, query="rare target", limit=1)[0].id for ns in namespaces]
        concurrent(
            "metadata_patch",
            args.users,
            lambda user: (
                clients[user].patch_entity_metadata(namespace(user), target_ids[user if args.isolated else 0], {f"user_{user}": True}).id
            ),
        )
        for index, ns in enumerate(namespaces):
            target = clients[0].backend.search_entities(ns, query="rare target", limit=1)[0]
            expected = {f"user_{user}" for user in (range(args.users) if not args.isolated else [index])}
            emit("metadata_integrity", namespace_index=index, missing=sorted(expected - target.metadata.keys()), expected=len(expected))
            if args.backend == "milvus":
                native = clients[0].backend.milvus.query(
                    ns, filter='metadata["needle"] == true', output_fields=["id", "metadata"], limit=args.users * 2
                )
                emit("native_metadata", namespace_index=index, rows=[dict(row) for row in native])
        start_counts = [clients[0].get_namespace_details(ns).num_entities for ns in namespaces]
        for iteration in range(args.rounds):
            concurrent(
                f"append_{iteration}",
                args.users,
                lambda user: len(
                    clients[user].update_entities(
                        namespace(user), [Entity(type="note", content=f"user-{user}-round-{iteration}", metadata={"stress": True})], False
                    )
                ),
            )
        # Explicit larger limit avoids mistaking the public API's default limit for lost data.
        for index, ns in enumerate(namespaces):
            found = clients[0].backend.scan_entities(ns, filters={"metadata.stress": True}, limit=args.users * args.rounds + 10)
            expected = {
                f"user-{user}-round-{iteration}"
                for user in (range(args.users) if not args.isolated else [index])
                for iteration in range(args.rounds)
            }
            actual = {entity.content for entity in found}
            if args.backend == "milvus":
                native = clients[0].backend.milvus.query(
                    ns, filter='metadata["stress"] == true', output_fields=["id", "content"], limit=args.users * args.rounds + 10
                )
                native_contents = {row["content"] for row in native}
                emit(
                    "native_append_integrity",
                    namespace_index=index,
                    missing=sorted(expected - native_contents),
                    foreign=sorted(native_contents - expected),
                    found=len(native),
                )
            emit(
                "append_integrity",
                namespace_index=index,
                expected=len(expected),
                found=len(found),
                missing=sorted(expected - actual),
                foreign=sorted(actual - expected),
                count_before=start_counts[index],
                count_after=clients[0].get_namespace_details(ns).num_entities,
            )
        if args.backend == "milvus":
            # Separate transient visibility during concurrent upserts from a
            # settled lost-update observation; request strong native reads too.
            time.sleep(5)
            for index, ns in enumerate(namespaces):
                rows = clients[0].backend.milvus.query(
                    ns,
                    filter='metadata["needle"] == true',
                    output_fields=["id", "metadata"],
                    limit=args.users * 2,
                    consistency_level="Strong",
                )
                emit("settled_metadata", namespace_index=index, rows=[dict(row) for row in rows])
        if args.policy_probe and args.backend == "milvus":
            from altk_evolve.config.hooks import HooksConfig, HookPluginSpec
            from altk_evolve.hooks.manager import initialize_hooks, shutdown_hooks

            client = clients[0]
            control = "stress_control_" + uuid.uuid4().hex
            client.create_namespace(control)
            stack.callback(client.delete_namespace, control)
            control_id = client.update_entities(
                control, [Entity(type="note", content="held control", metadata={"legal_hold": True})], False
            )[0].id
            target_id = target_ids[0]
            client.patch_entity_metadata(namespaces[0], target_id, {"legal_hold": True})
            initialize_hooks(
                HooksConfig(
                    plugins=[
                        HookPluginSpec(
                            name="legal_hold",
                            kind="altk_evolve.hooks.plugins.legal_hold.LegalHoldMemoryPlugin",
                            hooks=["memory_pre_delete"],
                            mode="sequential",
                            on_error="fail",
                        )
                    ]
                )
            )
            try:
                for label, ns, entity_id in [("one_entity_control", control, control_id), ("beyond_window", namespaces[0], target_id)]:
                    error = None
                    try:
                        client.delete_entity_by_id(ns, entity_id)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    found = client.backend.milvus.query(
                        ns, filter=f"id == {int(entity_id)}", output_fields=["id", "metadata"], limit=1, consistency_level="Strong"
                    )
                    emit("legal_hold_probe", case=label, error=error, remaining=len(found))
            finally:
                shutdown_hooks()
        if args.backend != "milvus":
            for client in clients:
                client.processing.registry.register(MemoryProcessor)
            plans = [client.processing.validate({"processors": [{"id": "memory", "plugin": MemoryProcessor.id}]}) for client in clients]

            def process(user):
                result = clients[user].process_trajectory(
                    {
                        "messages": [{"role": "user", "content": "tracked contribution"}],
                        "batch": {"source": "stress", "conversation_id": "ongoing", "batch_id": "same-delivery"},
                    },
                    namespace_id=namespace(user),
                    plan=plans[user],
                )
                return {"completed": len(result.completed_processors), "skipped": len(result.skipped_processors)}

            concurrent("duplicate_batch", args.users, process)
            concurrent("replay_batch", args.users, process)
            if args.backend == "postgres":
                from psycopg import sql

                backend = clients[0].backend
                table = sql.Identifier(backend._table_name(namespaces[0]))
                for label, query, params in [
                    ("count", sql.SQL("SELECT COUNT(*) FROM {}").format(table), ()),
                    ("metadata", sql.SQL("SELECT id FROM {} WHERE metadata @> %s::jsonb LIMIT 1").format(table), ('{"needle": true}',)),
                    (
                        "vector",
                        sql.SQL("SELECT id FROM {} ORDER BY embedding <=> %s::vector LIMIT 1").format(table),
                        (str(Embeddings().encode("rare").tolist()),),
                    ),
                ]:
                    plan = backend.conn.execute(sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ") + query, params).fetchone()[0]
                    emit("query_plan", query=label, plan=plan)
                # Diagnostic A/B/A only: still check the entity table exists, but
                # temporarily omit namespace metadata/count retrieval. Not a fix.
                with ExitStack() as diagnostic:
                    for client in clients:
                        diagnostic.enter_context(
                            patch.object(client, "get_namespace_details", side_effect=client.backend._validate_namespace)
                        )
                    concurrent("diagnostic_replay_without_count", args.users, process)
                concurrent("replay_restored", args.users, process)
            for index, ns in enumerate(namespaces):
                found = clients[0].backend.scan_entities(ns, filters={"content": "tracked contribution"}, limit=args.users + 1)
                emit("batch_integrity", namespace_index=index, outputs=len(found), expected=1)
        if args.reconcile and args.backend != "milvus":
            from altk_evolve.schema.conflict_resolution import EntityUpdate

            barrier = Barrier(args.users)

            def reconcile(old, new, **kwargs):
                target = next(entity for entity in old if entity.metadata.get("needle"))
                barrier.wait(timeout=60)
                return [EntityUpdate(id=target.id, type="note", content="rare target reconciled", event="UPDATE", metadata=new[0].metadata)]

            def replacement(user):
                backend = clients[user].backend
                ns = namespace(user)
                prepared = backend.prepare_updates(ns, [Entity(type="note", content="rare target", metadata={"writer": user})])
                backend.commit_prepared(ns, [prepared], checkpoint=(f"replace-{user}", {"writer": user}))
                return "committed"

            with patch("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", reconcile):
                results = concurrent("competing_replacements", args.users, replacement)
            for user, result in enumerate(results):
                record = clients[user].backend.get_processing_checkpoint(namespace(user), f"replace-{user}")
                emit("replacement_integrity", user=user, success="value" in result, checkpoint=record is not None)
        emit(
            "resources",
            max_rss_native=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            local_bytes=sum(p.stat().st_size for p in Path(tmp).rglob("*") if p.is_file()),
        )


if __name__ == "__main__":
    main()
