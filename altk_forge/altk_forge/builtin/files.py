"""Converting Langfuse trace exports (GET /api/public/traces/<id>, saved as *.langfuse.json) in a folder, offline,
e.g. to test a converter change. Pipelines convert traces read straight from Langfuse instead.

Writes <out>/<trace_id>.trajectory.json and <out>/index.json (a summary list the UI reads). Unchanged traces are
skipped unless force is given. A trace that fails to convert or validate is reported and left out of the index."""

import json
import traceback

from ..trajectory.summary import summary
from ..trajectory.validate import check, span_ids, validator_for
from .langfuse import convert


def convert_one(trace, host=None):
    """(trajectory, problems) for one raw trace; problems is empty when it converted and validated."""
    doc = convert(trace, host)
    problems = [f"{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}" for e in validator_for(doc).iter_errors(doc)]
    problems += check(doc, span_ids(doc))
    return doc, problems


def build(traces, out, host=None, force=False, log=print, should_stop=lambda: False, on_result=lambda kind: None):
    """Convert traces/*.langfuse.json into out/; returns (built, skipped, failed [(trace_id, problems)]).
    on_result is called with "built", "skipped" or "failed" after each trace."""
    out.mkdir(parents=True, exist_ok=True)
    built = skipped = 0
    failed, index = [], []
    sources = sorted(traces.glob("*.langfuse.json"))
    for i, src in enumerate(sources, 1):
        if should_stop():
            log("Stopped.")
            break
        trace_id = src.name.removesuffix(".langfuse.json")
        dst = out / f"{trace_id}.trajectory.json"
        try:
            if dst.exists() and not force and dst.stat().st_mtime >= src.stat().st_mtime:
                doc = json.loads(dst.read_text())
                skipped += 1
                on_result("skipped")
            else:
                doc, problems = convert_one(json.loads(src.read_text()), host)
                if problems:
                    failed.append((trace_id, problems[:3]))
                    on_result("failed")
                    log(f"[{i}/{len(sources)}] FAIL {trace_id}: {problems[0]}")
                    continue
                dst.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
                built += 1
                on_result("built")
                log(f"[{i}/{len(sources)}] {trace_id}")
            index.append(summary(doc, dst.name))
        except Exception as e:  # one bad trace must not stop the batch
            failed.append((trace_id, [f"{type(e).__name__}: {e}", traceback.format_exc().splitlines()[-3].strip()]))
            on_result("failed")
            log(f"[{i}/{len(sources)}] FAIL {trace_id}: {type(e).__name__}: {e}")

    index.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    (out / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
    return built, skipped, failed
