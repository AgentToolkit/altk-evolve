"""Which converter handles a trace, decided by the trace's JSON Schema (converters.schema):

  same       nothing new: the converter is reused, no LLM call;
  wider      the trace adds (new properties, a type where only null was seen) and changes no type: the LLM writes a new
             version of the converter, which replaces the current one. It must produce exactly the same trajectories
             for the traces the converter was built on (its samples), and a valid, faithful one for the new trace;
             when it cannot, a separate converter is written instead;
  different  a value has a type never seen there: a new converter is written for this schema.

A key the converter's traces always had but this one lacks does not count: a narrower trace is the same schema."""

from ..core.clock import now
from ..core.store import read_json, write_json
from .checks import fidelity_problems, problems_of
from .errors import ConverterError
from .sandbox import run
from .schema import compare_schema, decode, infer_schema, verdict_of
from .store import converter_ids, retire, root_of, samples, schema_of
from .writer import replace, write_new


def choose(pipeline_id, schema):
    """[(cid, verdict, diff)] of the pipeline's converters, best first: same, then wider (fewest additions), then
    different (fewest differences); among equals the converter that converted more traces."""
    rank = {"same": 0, "wider": 1, "different": 2}
    out = []
    for cid in converter_ids(pipeline_id):
        d = root_of(pipeline_id) / cid
        if read_json(d / "meta.json", {}).get("retired_at"):
            continue
        diff = compare_schema(schema, schema_of(d))
        verdict = verdict_of(diff)
        size = len(diff["added"]) + len(diff["changed"])
        out.append((rank[verdict], size, -read_json(d / "meta.json", {}).get("traces", 0), cid, verdict, diff))
    out.sort(key=lambda x: x[:3])
    return [x[3:] for x in out]


def _summary(diff):
    parts = [f"{len(diff[k])} {k}" for k in ("added", "changed", "missing") if diff[k]]
    return ", ".join(parts) or "no difference"


def convert_trace(pipeline_id, raw, context, log=lambda *_: None, may_write=True):
    """(trajectory, problems, info) for one raw trace. info says which converter made it and what changed:
    {converter, version, verdict, changed}. Raises ConverterError when it needs a new or changed converter and
    may_write is False; LLMError when the LLM is not available."""
    trace = decode(raw)
    trace_id = str(trace.get("id") or "trace")
    schema = infer_schema(trace)
    ranked = choose(pipeline_id, schema)
    best = ranked[0] if ranked else None
    if best and best[1] == "same":
        cid = best[0]
        d = root_of(pipeline_id) / cid
        result = run((d / "converter.py").read_text(), [{"trace": trace, "context": context}])[0]
        problems = (
            [result.get("error")] if not result.get("ok") else problems_of(result["result"]) + fidelity_problems(trace, result["result"])
        )
        meta = read_json(d / "meta.json")
        if not problems:
            meta["traces"] = meta.get("traces", 0) + 1
            meta["last_used_at"] = now()
            write_json(d / "meta.json", meta)
            return result["result"], [], {"converter": cid, "version": meta["version"], "verdict": "same", "changed": None}
        # Same schema, but the code does not cope with this trace's values: a new version that does.
        if not may_write:
            raise ConverterError(f"converter {cid} failed: {problems[0]}")
        # A converter whose own samples break today's checks (written before a check existed) cannot be repaired:
        # the repair must reproduce those samples exactly. It is replaced by a new converter, written from it.
        stale = [p for _, t, e in samples(d) for p in problems_of(e) + fidelity_problems(t, e)]
        if stale:
            log(f"Converter {cid} breaks the current checks on its own samples ({stale[0][:200]}); writing a new one")
            new, doc = write_new(
                pipeline_id, trace_id, trace, schema, context, f"{cid} breaks the current checks: {stale[0][:150]}", base=cid, log=log
            )
            retire(pipeline_id, cid, f"replaced by {new}: {stale[0][:200]}")
            return doc, [], {"converter": new, "version": 1, "verdict": "same", "changed": "created"}
        try:
            doc = replace(
                pipeline_id, cid, trace_id, trace, schema, [], context, "repair: " + problems[0][:200], log, failure="; ".join(problems[:5])
            )
        except ConverterError as e:
            log(f"Converter {cid} could not be repaired for this trace ({e}); writing a new one from it")
            new, doc = write_new(
                pipeline_id,
                trace_id,
                trace,
                schema,
                context,
                f"repairing {cid} would have changed its earlier trajectories",
                base=cid,
                log=log,
            )
            return doc, [], {"converter": new, "version": 1, "verdict": "same", "changed": "created"}
        return doc, [], {"converter": cid, "version": read_json(d / "meta.json")["version"], "verdict": "same", "changed": "repaired"}
    if not may_write:
        raise ConverterError("this trace needs a new or extended converter")
    if best and best[1] == "wider":
        cid, _, diff = best
        try:
            doc = replace(
                pipeline_id,
                cid,
                trace_id,
                trace,
                schema,
                diff["added"],
                context,
                f"the schema is wider: {len(diff['added'])} added, no type changed",
                log,
            )
            return (
                doc,
                [],
                {
                    "converter": cid,
                    "version": read_json(root_of(pipeline_id) / cid / "meta.json")["version"],
                    "verdict": "wider",
                    "changed": "extended",
                },
            )
        except ConverterError as e:
            log(f"Converter {cid} could not be extended safely ({e}); writing a new one from it")
            cid2, doc = write_new(
                pipeline_id,
                trace_id,
                trace,
                schema,
                context,
                f"extending {cid} would have changed its earlier trajectories",
                base=cid,
                log=log,
            )
            return doc, [], {"converter": cid2, "version": 1, "verdict": "wider", "changed": "created"}
    if best:
        d = best[2]
        shown = d["changed"][:3]
        reason = f"the schema changed against {best[0]} ({_summary(d)}: {'; '.join(shown)})"
    else:
        reason = "first trace of this pipeline"
    cid, doc = write_new(pipeline_id, trace_id, trace, schema, context, reason, log=log)
    return doc, [], {"converter": cid, "version": 1, "verdict": "different" if best else "new", "changed": "created"}
