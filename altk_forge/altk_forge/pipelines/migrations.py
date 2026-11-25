"""Moving data written by earlier versions of Forge to the current layout. Run at start-up; each is a no-op once done."""

import json
import shutil

from ..core.clock import now
from ..core.paths import DATA, PIPES, pipe_dir
from ..core.store import LOCK, write_json
from .filters import save_filter
from .integrations import INTEGRATIONS, all_integrations
from .kinds import MEMORY_FIELDS
from .state import all_pipelines
from .trajectories import write_index


def migrate_memory_settings():
    """Memory settings used to live on the altk-evolve integration; they belong to the pipeline. Copy them to every
    pipeline that writes to that integration and has none yet, then keep only the connection on the integration."""
    with LOCK:
        items = all_integrations()
        moved = {}
        for i in items:
            stale = [f for f in i if f in MEMORY_FIELDS or f == "manage_profile"] if i["kind"] == "altk_evolve" else []
            if stale:
                old = {f: i.pop(f) for f in stale}
                moved[i["id"]] = {f: v for f, v in old.items() if v not in (None, "") and f in MEMORY_FIELDS}
        if not moved:
            return []
        changed = []
        for p in all_pipelines():
            settings = moved.get(p.get("output_id"))
            if settings and not p.get("memory"):
                p["memory"] = settings
                write_json(pipe_dir(p["id"]) / "pipeline.json", p)
                changed.append(p["id"])
        write_json(INTEGRATIONS, items, private=True)
    return changed


def migrate_experiments():
    """.data/exp/<id> (experiments with downloaded traces) -> a filter per experiment and a test pipeline that keeps
    the converted trajectories. Downloaded raw traces are not kept: they are read from the source now."""
    old = DATA / "exp"
    if not old.exists():
        return []
    made = []
    for meta_path in sorted(old.glob("*/experiment.json")):
        e = json.loads(meta_path.read_text())
        if (PIPES / e["id"]).exists():
            continue
        f = e.get("filter") or {}
        criteria = {
            "name": {"op": "starts with", "value": f["name_prefix"]} if f.get("name_prefix") else None,
            "from": f.get("from"),
            "to": f.get("to"),
            "tz": f.get("tz"),
        }
        flt = save_filter(
            {
                "kind": "langfuse",
                "name": e["name"],
                "description": f"Migrated from experiment {e['id']}",
                "criteria": {k: v for k, v in criteria.items() if v},
            }
        )
        root = PIPES / e["id"]
        (root / "trajectories").mkdir(parents=True, exist_ok=True)
        traces = {}
        for src in sorted((meta_path.parent / "trajectories").glob("*.trajectory.json")):
            shutil.copy2(src, root / "trajectories" / src.name)
            doc = json.loads(src.read_text())
            raw = meta_path.parent / "traces" / f"{doc['trace_id']}.langfuse.json"
            updated = json.loads(raw.read_text()).get("updatedAt") if raw.exists() else None
            traces[doc["trace_id"]] = {
                "status": "built",
                "timestamp": doc.get("timestamp"),
                "updated_at": updated,
                "integration_id": e["integration_id"],
                "filters": [flt["id"]],
                "processed_at": now(),
                "migrated": True,
            }
        write_json(root / "state.json", {"traces": traces, "watermarks": {}})
        p = {
            "id": e["id"],
            "name": e["name"],
            "description": e.get("description"),
            "created_at": e.get("created_at") or now(),
            "inputs": [{"integration_id": e["integration_id"], "filter_ids": [flt["id"]]}],
            "output_id": None,
            "mode": "test",
            "enabled": False,
            "poll_seconds": 60,
            "settle_seconds": 120,
            "updated_at": now(),
            "runs": [],
            "migrated_from": f".data/exp/{e['id']}",
        }
        write_json(root / "pipeline.json", p)
        write_index(e["id"])
        made.append(e["id"])
    return made
