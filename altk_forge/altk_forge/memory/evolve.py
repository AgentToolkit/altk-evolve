"""Output integration: altk-evolve (https://github.com/AgentToolkit/altk-evolve), the memory that generates guidelines.

A trajectory is sent as slices, one per llm call (see trajectory.slices), each through POST /api/trajectories:
  {namespace_id, trajectory: {messages, tools, trace_id, model, metadata, outcome, batch}, processing_profile: {id, revision}}
That route is what save_trajectory uses when it is given a processing profile; the profile carries the generation
settings (guidelines_mode, consistency_method, guidelines_model, custom_llm_provider, segmentation, trimming), which
save_trajectory itself cannot take per call. The profile is created or refreshed from the pipeline's settings
before sending (PUT /api/processing-profiles/<id>).

What one slice writes is recorded (namespace and entity ids), so a test pipeline can delete exactly what it produced."""

import re
import urllib.parse

from ..sources.http import SourceError, request

# The settings of the evolve.guidelines processor (altk_evolve/processing/builtin.py, GuidelineConfig); all optional,
# an empty one follows the evolve server's environment (EVOLVE_GUIDELINES_MODEL, EVOLVE_GUIDELINES_MODE, ...).
GUIDELINE_FIELDS = [
    "guidelines_mode",
    "consistency_method",
    "guidelines_model",
    "custom_llm_provider",
    "segmentation_enabled",
    "trajectory_max_steps",
    "trajectory_max_step_chars",
    "trajectory_tail_steps",
]


class Evolve:
    def __init__(self, host, api_key=None, **settings):
        self.host = host.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.s = settings

    def call(self, method, path, body=None, headers=None):
        return request(method, f"{self.host}/api/{path}", {**self.headers, **(headers or {})}, body, timeout=600, label="altk-evolve")

    # ---- connection
    def check(self):
        _, namespaces, _ = self.call("GET", "namespaces")
        _, processors, _ = self.call("GET", "processors")
        names = [p.get("id") or p.get("plugin") for p in (processors or [])] if isinstance(processors, list) else []
        return f"{len(namespaces or [])} namespaces" + (f", processors {', '.join(map(str, names))}" if names else "")

    # ---- processing profile
    def profile_id(self):
        return self.s.get("processing_profile") or "forge"

    def profile_definition(self):
        config = {}
        for f in GUIDELINE_FIELDS:
            v = self.s.get(f)
            if v in (None, ""):
                continue
            if f == "segmentation_enabled":
                v = v in (True, "true", "1")
            elif f.startswith("trajectory_"):
                v = int(v)
            config[f] = v
        return {"schema_version": 1, "processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": config}]}

    def ensure_profile(self):
        """Create the profile, or move it to the integration's settings; returns its revision. A profile the user
        named explicitly and pinned (profile_revision) is used as it is."""
        pid = self.profile_id()
        if self.s.get("profile_revision"):
            return int(self.s["profile_revision"])
        if self.s.get("processing_profile") and not self.s.get("manage_profile", True):
            return None
        definition = self.profile_definition()
        path = f"processing-profiles/{urllib.parse.quote(pid)}"
        try:
            _, current, _ = self.call("GET", path)
        except SourceError as e:
            if e.code != 404:
                raise
            current = None
        if current is None:
            _, made, _ = self.call("PUT", path, definition, {"If-None-Match": "*"})
            return made["revision"]
        wanted = definition["processors"][0]["config"]
        have = ((current.get("manifest") or {}).get("processors") or [{}])[0].get("config") or {}
        if all(have.get(k) == v for k, v in wanted.items()):
            return current["revision"]
        _, made, _ = self.call("PUT", path, definition, {"If-Match": f'"{current["revision"]}"'})
        return made["revision"]

    # ---- namespaces
    def ensure_namespace(self, ns, known):
        if ns in known:
            return
        try:
            self.call("POST", "namespaces", {"namespace_id": ns})
        except SourceError as e:
            if e.code != 400:  # 400 = it exists already
                raise
        known.add(ns)

    # ---- one slice
    def trajectory_fields(self):
        """Fields this evolve server's Trajectory model accepts (it forbids others; older versions have no
        `outcome`), read once from its OpenAPI schema. None when the schema cannot be read."""
        if not hasattr(self, "_fields"):
            try:
                _, spec, _ = request("GET", f"{self.host}/openapi.json", self.headers, timeout=20, label="altk-evolve")
                self._fields = set(spec["components"]["schemas"]["Trajectory"]["properties"])
            except (SourceError, KeyError, TypeError):
                self._fields = None
        return self._fields

    def send(self, piece, revision, known_namespaces):
        """Process one slice; returns {namespace, entity_ids, guidelines: [text], operation_id}."""
        ns = piece["namespace_id"]
        self.ensure_namespace(ns, known_namespaces)
        trajectory = dict(piece["trajectory"])
        if trajectory.get("outcome"):  # older evolve reads the evidence from metadata instead of `outcome`
            trajectory["metadata"] = {
                **trajectory.get("metadata", {}),
                "evidence": "success" if trajectory["outcome"]["success"] else "failure",
            }
        fields = self.trajectory_fields()
        if fields:
            trajectory = {k: v for k, v in trajectory.items() if k in fields}
        body = {
            "namespace_id": ns,
            "trajectory": trajectory,
            "processing_profile": {"id": self.profile_id(), **({"revision": revision} if revision else {})},
        }
        for _ in range(len(trajectory)):
            try:
                _, result, _ = self.call("POST", "trajectories", body)
                break
            except SourceError as e:
                # Schema unknown: drop the field the server names as forbidden and try again.
                extra = re.findall(r'"extra_forbidden","loc":\["body","trajectory","(\w+)"\]', str(e))
                if e.code != 422 or not extra or extra[0] not in body["trajectory"]:
                    raise
                body["trajectory"] = {k: v for k, v in body["trajectory"].items() if k != extra[0]}
        result = result or {}
        # `updates` lists what the write did to the namespace: the entities this slice added, and existing ones that
        # conflict resolution merged it into (often from an earlier slice of the same turn). Only the added ones belong
        # to this slice; the others are recorded apart, so nothing is counted twice.
        created, touched = set(), set()
        for u in result.get("updates") or []:
            uid = str(u.get("id") or u.get("entity_id") or "")
            event = str(u.get("event") or "ADD").upper()
            if uid and event == "ADD":
                created.add(uid)
            elif uid and event in ("UPDATE", "DELETE"):
                touched.add(uid)  # NONE: an existing entity the write left as it was
        created |= {str(e["id"]) for e in result.get("entities") or [] if e.get("id")}
        guidelines = [e.get("content") for e in result.get("entities") or [] if e.get("type") == "guideline"]
        return {
            "namespace": ns,
            "entity_ids": sorted(created, key=_num),
            "updated_ids": sorted(touched - created, key=_num),
            "guidelines": guidelines,
            "operation_id": result.get("operation_id"),
        }

    # ---- removal
    def entities(self, ns, limit=500):
        _, data, _ = self.call("GET", f"namespaces/{urllib.parse.quote(ns)}/entities?limit={limit}")
        return data or []

    def delete_entity(self, ns, entity_id):
        try:
            self.call("DELETE", f"namespaces/{urllib.parse.quote(ns)}/entities/{urllib.parse.quote(entity_id)}")
            return True
        except SourceError as e:
            if e.code in (400, 404):
                return False  # already gone
            raise

    def delete_for_traces(self, ns, entity_ids, trace_ids):
        """Delete the recorded entities, plus any in the namespace generated from these traces (guidelines merged
        later carry the source trace in metadata). Returns how many were deleted."""
        ids = set(entity_ids)
        try:
            for e in self.entities(ns):
                meta = e.get("metadata") or {}
                src = str(meta.get("source_task_id") or meta.get("trace_id") or meta.get("task_id") or "")
                if src and any(src == t or src.startswith(f"{t}:") for t in trace_ids):
                    ids.add(e["id"])
        except SourceError:
            pass  # listing is best effort; the recorded ids still go
        return sum(self.delete_entity(ns, i) for i in sorted(ids))

    def guidelines(self, ns, limit=200):
        return [e for e in self.entities(ns, limit) if e.get("type") == "guideline"]


def _num(x):
    return (0, int(x)) if str(x).isdigit() else (1, str(x))
