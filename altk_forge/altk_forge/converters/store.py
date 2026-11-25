"""The converters of a pipeline on disk.

  .data/pipelines/<id>/converters/<cid>/converter.py        the current code
  .data/pipelines/<id>/converters/<cid>/versions/v<n>.py    every version
  .data/pipelines/<id>/converters/<cid>/schema.json         the JSON Schema of the traces it handles
  .data/pipelines/<id>/converters/<cid>/meta.json           version, history (why each version was written), counts
  .data/pipelines/<id>/converters/<cid>/samples/            a few decoded traces with the trajectory the converter made
                                                           of them: the regression set for the next version

Each converter keeps its schema: the merge of the schemas of every trace it converted, so it grows with each version."""

import difflib
import json
import shutil

from ..core.clock import now
from ..core.paths import pipe_dir
from ..core.store import read_json, write_json
from .errors import ConverterError
from .sandbox import run
from .schema import infer_schema, merge_schema, schema_paths

MAX_SAMPLES = 6  # traces kept per converter for the regression check


def root_of(pipeline_id):
    return pipe_dir(pipeline_id) / "converters"


def converter_ids(pipeline_id):
    root = root_of(pipeline_id)
    ids = [d.name for d in root.iterdir() if (d / "meta.json").exists()] if root.exists() else []
    return sorted(ids, key=lambda c: int(c[1:]) if c[1:].isdigit() else 0)


def list_converters(pipeline_id):
    out = []
    for cid in converter_ids(pipeline_id):
        d = root_of(pipeline_id) / cid
        meta = read_json(d / "meta.json", {})
        out.append(
            {
                **{k: v for k, v in meta.items() if k != "history"},
                "attributes": len(schema_paths(schema_of(d))),
                "last_change": (meta.get("history") or [{}])[-1],
            }
        )
    return out


def converter_detail(pipeline_id, cid, version=None):
    if cid not in converter_ids(pipeline_id):
        return None
    d = root_of(pipeline_id) / cid
    meta = read_json(d / "meta.json", {})
    v = int(version) if version not in (None, "") else meta["version"]
    path = d / "versions" / f"v{v}.py"
    if not path.exists():
        return None
    code = path.read_text()
    previous = d / "versions" / f"v{v - 1}.py"
    diff = (
        "".join(difflib.unified_diff(previous.read_text().splitlines(True), code.splitlines(True), f"v{v - 1}", f"v{v}"))
        if previous.exists()
        else None
    )
    schema = schema_of(d)
    return {
        **meta,
        "shown_version": v,
        "code": code,
        "diff": diff,
        "schema": schema,
        "attributes": len(schema_paths(schema)),
        "samples": [p.name.removesuffix(".trace.json") for p in sorted((d / "samples").glob("*.trace.json"))],
    }


def schema_of(d):
    """The schema a converter handles (schema.json; for a converter stored before schemas, inferred from its samples)."""
    schema = read_json(d / "schema.json")
    if schema is None:
        for p in sorted((d / "samples").glob("*.trace.json")):
            schema = merge_schema(schema, infer_schema(json.loads(p.read_text())))
        schema = schema or {}
        if schema:
            write_json(d / "schema.json", schema)
    return schema


def save_schema(d, schema):
    write_json(
        d / "schema.json",
        {"$schema": "https://json-schema.org/draft/2020-12/schema", **{k: v for k, v in schema.items() if k != "$schema"}},
    )


def retire(pipeline_id, cid, reason):
    """Take a converter out of use (choose() skips it) and keep it for its history."""
    d = root_of(pipeline_id) / cid
    meta = read_json(d / "meta.json", {})
    meta.update(retired_at=now(), retired_reason=reason)
    write_json(d / "meta.json", meta)


def delete_converter(pipeline_id, cid):
    if cid not in converter_ids(pipeline_id):
        raise ConverterError(f"No converter {cid}")
    shutil.rmtree(root_of(pipeline_id) / cid)


def new_id(pipeline_id):
    taken = [int(c[1:]) for c in converter_ids(pipeline_id) if c[1:].isdigit()]
    return f"c{max(taken, default=0) + 1}"


def samples(d):
    out = []
    for p in sorted((d / "samples").glob("*.trace.json")):
        expected = p.with_name(p.name.replace(".trace.json", ".expected.json"))
        if expected.exists():
            out.append((p.name.removesuffix(".trace.json"), json.loads(p.read_text()), json.loads(expected.read_text())))
    return out


def save_version(d, meta, code, action, trace_id, reason, usage, attempts, extra=None):
    meta["version"] = meta.get("version", 0) + 1
    (d / "versions").mkdir(parents=True, exist_ok=True)
    (d / "versions" / f"v{meta['version']}.py").write_text(code)
    (d / "converter.py").write_text(code)
    meta.setdefault("history", []).append(
        {
            "version": meta["version"],
            "at": now(),
            "action": action,
            "trace_id": trace_id,
            "reason": reason,
            "attempts": attempts,
            "usage": usage,
            **(extra or {}),
        }
    )
    meta["updated_at"] = now()


def add_sample(d, trace_id, trace, doc):
    s = d / "samples"
    s.mkdir(parents=True, exist_ok=True)
    kept = sorted(s.glob("*.trace.json"), key=lambda p: p.stat().st_mtime)
    if len(kept) >= MAX_SAMPLES:  # keep the first sample (the one it was written for), drop the oldest of the rest
        for p in kept[1:2]:
            p.unlink()
            p.with_name(p.name.replace(".trace.json", ".expected.json")).unlink(missing_ok=True)
    (s / f"{trace_id}.trace.json").write_text(json.dumps(trace, ensure_ascii=False))
    (s / f"{trace_id}.expected.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))


def refresh_samples(d, code, context):
    """After a change, the samples' expected trajectories are what the new code makes (identical for the old ones)."""
    regression = samples(d)
    for (tid, t, _), r in zip(regression, run(code, [{"trace": t, "context": context} for _, t, _ in regression])):
        if r.get("ok"):
            (d / "samples" / f"{tid}.expected.json").write_text(json.dumps(r["result"], ensure_ascii=False, indent=1))
