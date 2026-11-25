"""Having the LLM write a converter, or a new version of one, until the code passes every check.

The model's code is run on the trace and on the converter's regression samples; the problems are fed back to it, up
to ATTEMPTS times. A new version must reproduce the samples' trajectories exactly."""

from ..core.clock import now
from ..core.paths import PACKAGE
from ..core.store import read_json, write_json
from ..llm import chat
from .checks import fidelity_problems, first_differences, problems_of
from .errors import ConverterError
from .prompts import code_of, prompt_extend, prompt_new
from .sandbox import run
from .schema import merge_schema, schema_paths
from .store import add_sample, new_id, refresh_samples, root_of, samples, save_schema, save_version, schema_of

REFERENCE = PACKAGE / "builtin" / "langfuse.py"


ATTEMPTS = 3  # LLM attempts per change


def _verify(code, trace, context, regression):
    """Problems of `code`: it must convert `trace` into a valid trajectory faithful to the trace, and every
    (trace, expected) of the regression set into exactly the expected trajectory. Returns (problems, trajectory of
    `trace`)."""
    items = [{"trace": trace, "context": context}] + [{"trace": t, "context": context} for _, t, _ in regression]
    results = run(code, items)
    first = results[0]
    if not first.get("ok"):
        return [f"convert() failed on the new trace: {first.get('error')}"], None
    problems = [f"the trajectory is invalid: {p}" for p in problems_of(first["result"])[:12]]
    problems += (
        [f"the trajectory has trace_id {first['result'].get('trace_id')!r}; it must be str(trace['id']) = {str(trace.get('id'))!r}"]
        if isinstance(first["result"], dict) and "id" in trace and first["result"].get("trace_id") != str(trace["id"])
        else []
    )
    problems += [f"the trajectory leaves out what the trace has: {p}" for p in fidelity_problems(trace, first["result"])[:12]]
    for (tid, _, expected), r in zip(regression, results[1:]):
        if not r.get("ok"):
            problems.append(f"convert() now fails on earlier trace {tid}: {r.get('error')}")
        elif r["result"] != expected:
            diffs = first_differences(expected, r["result"])
            problems.append(f"the trajectory of earlier trace {tid} changed: " + "; ".join(diffs))
    return problems, first["result"]


def _converse(messages, verify, log=lambda *_: None, what="writing the converter"):
    """Ask the model, check its code, feed the problems back; returns (code, trajectory, usage, attempts). Each failed
    attempt is logged with its problems. Raises ConverterError when no attempt passes."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    problems = ["no answer"]
    for attempt in range(1, ATTEMPTS + 1):
        log(None, f"{what}: LLM attempt {attempt}/{ATTEMPTS}")
        answer, u = chat(messages)
        log(None, f"{what}: checking attempt {attempt}/{ATTEMPTS}")
        for k in usage:
            usage[k] += u.get(k) or 0
        usage["model"] = u.get("model")
        code = code_of(answer)
        if not code:
            problems = ["the answer had no ```python block"]
            log(f"  attempt {attempt}/{ATTEMPTS} rejected: the answer had no code")
            messages = messages + [
                {"role": "assistant", "content": answer[:2000]},
                {"role": "user", "content": "Answer with the complete file in one ```python block."},
            ]
            continue
        problems, doc = verify(code)
        if not problems:
            return code, doc, usage, attempt
        log(
            f"  attempt {attempt}/{ATTEMPTS} rejected, {len(problems)} problem{'s' * (len(problems) > 1)}:"
            + "".join(f"\n    - {p}" for p in problems[:10])
            + (f"\n    … {len(problems) - 10} more" if len(problems) > 10 else "")
        )
        messages = messages + [
            {"role": "assistant", "content": f"```python\n{code}```"},
            {
                "role": "user",
                "content": "That does not work yet:\n- "
                + "\n- ".join(problems[:15])
                + "\n\nFix it and answer with the complete file in one ```python block.",
            },
        ]
    raise ConverterError(
        f"no working converter after {ATTEMPTS} attempts; the last one: {'; '.join(problems[:3])}"
        + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
    )


def write_new(pipeline_id, trace_id, trace, schema, context, reason, base=None, log=lambda *_: None):
    """A new converter for this trace's schema. `base` (a converter id) is used as the starting point, else the
    built-in Langfuse converter is the reference."""
    if base:
        ref_code, ref_name = (root_of(pipeline_id) / base / "converter.py").read_text(), f"converter {base} of this pipeline"
    else:
        ref_code, ref_name = REFERENCE.read_text(), "the built-in converter for a LangGraph agent traced to Langfuse"
    log(f"Writing a new converter for trace {trace_id} ({reason})")
    code, doc, usage, attempts = _converse(
        prompt_new(trace, ref_code, ref_name),
        lambda c: _verify(c, trace, context, []),
        log,
        f"writing a new converter for {trace.get('name') or trace_id}",
    )
    cid = new_id(pipeline_id)
    d = root_of(pipeline_id) / cid
    meta = {
        "id": cid,
        "created_at": now(),
        "traces": 1,
        "based_on": base or "built-in reference",
        "first_trace": trace_id,
        "last_used_at": now(),
    }
    n = len(schema_paths(schema))
    save_version(d, meta, code, "created", trace_id, reason, usage, attempts, {"attributes": n})
    save_schema(d, schema)
    add_sample(d, trace_id, trace, doc)
    write_json(d / "meta.json", meta)
    log(f"Converter {cid} v1 written ({attempts} attempt{'s' * (attempts > 1)}, schema with {n} properties)")
    return cid, doc


def replace(pipeline_id, cid, trace_id, trace, schema, added, context, reason, log=lambda *_: None, failure=None):
    """A new version of converter `cid` that also handles this trace, replacing the current one; its samples must
    convert exactly as before. Its schema becomes the merge with the trace's."""
    d = root_of(pipeline_id) / cid
    meta, code = read_json(d / "meta.json"), (d / "converter.py").read_text()
    regression = samples(d)
    log(f"Rewriting converter {cid} for trace {trace_id} ({reason})")
    new_code, doc, usage, attempts = _converse(
        prompt_extend(code, trace, added, failure),
        lambda c: _verify(c, trace, context, regression),
        log,
        f"extending {cid} for {trace.get('name') or trace_id}",
    )
    action = "repaired" if failure else "extended"
    save_version(
        d,
        meta,
        new_code,
        action,
        trace_id,
        reason,
        usage,
        attempts,
        {"added": len(added), **({"code_unchanged": True} if new_code == code else {})},
    )
    refresh_samples(d, new_code, context)
    save_schema(d, merge_schema(schema_of(d), schema))
    add_sample(d, trace_id, trace, doc)
    meta["traces"] = meta.get("traces", 0) + 1
    meta["last_used_at"] = now()
    write_json(d / "meta.json", meta)
    log(
        f"Converter {cid} v{meta['version']} replaces v{meta['version'] - 1}: {action}, "
        f"the {len(regression)} earlier samples convert as before"
    )
    return doc
