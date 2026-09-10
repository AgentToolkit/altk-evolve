"""Opt-in REST router for hosts that inject authentication and service-instance scope.

The legacy standalone dashboard remains separate. This router has no unauthenticated
or global-namespace defaults and is not mounted automatically by the MCP server.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.frontend.services.context import use_client
from altk_evolve.frontend.services.schedules import ScheduleService, preview
from altk_evolve.retention.schedule import CronJobSpec, ScheduleDefinition
from altk_evolve.retention.policy import RetentionPolicy
from altk_evolve.retention.store import RetentionStore


class MemoryScope(BaseModel):
    """Trusted server-side context; never construct this from request body fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    namespace_id: str
    user_id: str | None = None
    agent_id: str | None = None
    can_manage: bool = False

    @field_validator("namespace_id", "user_id", "agent_id")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("scope identifiers must be nonblank")
        return value


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetadataPatch(Body):
    metadata: dict[str, Any]


class AccessRequest(Body):
    entity_ids: list[str] = Field(min_length=1, max_length=200)


class FactRequest(Body):
    message: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyRequest(Body):
    name: str = Field(min_length=1)
    policy: RetentionPolicy
    description: str | None = None
    enabled: bool = True


class RunRequest(Body):
    policy_id: str
    dry_run: bool = True
    additional_matches: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


class ScheduleRequest(Body):
    definition: ScheduleDefinition
    expected_revision: int = Field(default=0, ge=0)


class RecoveryRequest(Body):
    worker_stopped: bool = False


class PreviewRequest(Body):
    spec: CronJobSpec
    after: str | None = None
    count: int = Field(default=5, ge=1, le=20)


def _result(value: str) -> dict[str, Any]:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise HTTPException(502, "Invalid service response")
    if "error" in result:
        message = str(result["error"])
        code = 403 if "Permission denied" in message else (404 if "not found" in message.lower() else 400)
        raise HTTPException(code, detail=message)
    return result


def _user(scope: MemoryScope) -> str:
    if scope.user_id is None or scope.user_id.strip() in {"default", "default_user"}:
        raise HTTPException(401, "A real user identity is required for personal memory")
    return scope.user_id


def _manager(scope: MemoryScope) -> str:
    if not scope.can_manage:
        raise HTTPException(403, "Manage access is required")
    return _user(scope)


SAFE_METADATA = {"title", "category", "created_at", "last_accessed", "legal_hold", "retention_flagged_at", "retention_rule"}


def _project(item: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    """Never expose private content or arbitrary metadata to admin inventory consumers."""
    projected = {key: item[key] for key in ("id", "type", "created_at") if key in item}
    projected["metadata"] = {key: value for key, value in item.get("metadata", {}).items() if key in SAFE_METADATA}
    if not admin:
        projected.update({key: item[key] for key in ("content", "content_preview") if key in item})
    return projected


def build_memory_router(*, client_dependency: Callable[..., Any], scope_dependency: Callable[..., Any]) -> APIRouter:
    """Build host-mountable routes using FastAPI's ordinary dependency injection.

    Scope dependencies must authenticate the caller and authorize any selected agent.
    They may also enforce the host's feature flag before returning MemoryScope.
    """
    router = APIRouter(tags=["Evolve memory"])

    def service(client: EvolveClient = Depends(client_dependency), scope: MemoryScope = Depends(scope_dependency)):
        if not isinstance(scope, MemoryScope):
            raise HTTPException(500, "Scope dependency must return MemoryScope")
        return client, scope

    def invoke(pair, tool_name: str, *, personal: bool = False, **args):
        from altk_evolve.frontend.mcp import mcp_server

        client, scope = pair
        if personal:
            args.update(user_id=_user(scope), agent_id=scope.agent_id)
        with use_client(client):
            return _result(getattr(mcp_server, tool_name)(namespace_id=scope.namespace_id, **args))

    def schedules(pair) -> ScheduleService:
        client, scope = pair
        _manager(scope)
        return ScheduleService(client, scope.namespace_id)

    def schedule_call(pair, method: str, *args, **kwargs):
        try:
            return getattr(schedules(pair), method)(*args, **kwargs)
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(
                409 if "conflict" in message.lower() else (404 if "not found" in message.lower() else 400), message
            ) from exc

    @router.get("/memory/entities")
    def inventory(limit: int = Query(50, ge=1, le=200), cursor: str | None = None, pair=Depends(service)):
        result = invoke(pair, "list_entities", personal=True, limit=limit, cursor=cursor, include_content=True)
        result["items"] = [_project(item, admin=False) for item in result["items"]]
        return result

    @router.get("/memory/entities/{entity_id}")
    def detail(entity_id: str, pair=Depends(service)):
        return _project(invoke(pair, "get_entity", personal=True, entity_id=entity_id, record_access=False), admin=False)

    @router.patch("/memory/entities/{entity_id}/metadata")
    def patch(entity_id: str, body: MetadataPatch, pair=Depends(service)):
        if set(body.metadata) - {"title", "category"}:
            raise HTTPException(400, "Only title and category can be edited")
        return _project(
            invoke(pair, "patch_entity_metadata", personal=True, entity_id=entity_id, metadata_patch=json.dumps(body.metadata)), admin=False
        )

    @router.delete("/memory/entities/{entity_id}")
    def delete(entity_id: str, pair=Depends(service)):
        return invoke(pair, "delete_entity", personal=True, entity_id=entity_id)

    @router.post("/memory/access")
    def access(body: AccessRequest, pair=Depends(service)):
        return invoke(pair, "record_access", personal=True, entity_ids=list(dict.fromkeys(body.entity_ids)))

    @router.post("/memory/facts")
    def store_facts(body: FactRequest, pair=Depends(service)):
        _, scope = pair
        if set(body.metadata) & {"owner_id", "user_id", "namespace_id", "agent_id", "legal_hold", "visibility"}:
            raise HTTPException(400, "Identity and protection metadata cannot be set through personal facts")
        metadata = {**body.metadata, **({"agent_id": scope.agent_id} if scope.agent_id else {})}
        return invoke(pair, "store_user_facts", user_id=_user(scope), message=body.message, metadata=json.dumps(metadata))

    @router.get("/memory/facts")
    def facts(query: str | None = None, limit: int = Query(5, ge=1, le=100), pair=Depends(service)):
        return invoke(pair, "retrieve_user_facts", user_id=_user(pair[1]), agent_id=pair[1].agent_id, query=query, limit=limit)

    @router.get("/manage/memory/entities")
    def admin_inventory(limit: int = Query(50, ge=1, le=200), cursor: str | None = None, pair=Depends(service)):
        _manager(pair[1])
        result = invoke(pair, "list_entities", agent_id=pair[1].agent_id, limit=limit, cursor=cursor)
        result["items"] = [_project(item, admin=True) for item in result["items"]]
        return result

    def admin_entity(pair, entity_id):
        client, scope = pair
        _manager(scope)
        filters = {"id": entity_id}
        if scope.agent_id:
            filters["metadata.agent_id"] = scope.agent_id
        matches = client.scan_entities(scope.namespace_id, filters=filters, limit=1)
        if not matches:
            raise HTTPException(404, "Entity not found")
        return matches[0]

    @router.get("/manage/memory/entities/{entity_id}")
    def admin_detail(entity_id: str, pair=Depends(service)):
        return _project(admin_entity(pair, entity_id).model_dump(mode="json"), admin=True)

    @router.patch("/manage/memory/entities/{entity_id}/metadata")
    def admin_patch(entity_id: str, body: MetadataPatch, pair=Depends(service)):
        if set(body.metadata) - {"title", "category", "legal_hold"}:
            raise HTTPException(400, "Only title, category, and legal_hold can be edited")
        admin_entity(pair, entity_id)
        client, scope = pair
        result = client.patch_entity_metadata(scope.namespace_id, entity_id, body.metadata)
        return _project(result.model_dump(mode="json"), admin=True)

    @router.get("/manage/retention/policies")
    def policies(pair=Depends(service)):
        _manager(pair[1])
        return invoke(pair, "list_retention_policies", include_disabled=True)

    @router.put("/manage/retention/policies/{policy_id}")
    def put_policy(policy_id: str, body: PolicyRequest, pair=Depends(service)):
        _manager(pair[1])
        return invoke(
            pair,
            "put_retention_policy",
            policy_id=policy_id,
            name=body.name,
            policy=body.policy.model_dump_json(),
            description=body.description,
            enabled=body.enabled,
        )

    @router.post("/manage/retention/runs")
    def run(body: RunRequest, pair=Depends(service)):
        from altk_evolve.frontend.mcp.mcp_server import _retention_audit_payload

        actor = _manager(pair[1])
        scope = pair[1]
        result = invoke(
            pair,
            "run_retention",
            policy_id=body.policy_id,
            dry_run=body.dry_run,
            actor_id=actor,
            metadata_filters=json.dumps({"agent_id": scope.agent_id}) if scope.agent_id else None,
            additional_matches=json.dumps(body.additional_matches),
        )
        return _retention_audit_payload(result)

    @router.get("/manage/retention/runs")
    def runs(limit: int = Query(50, ge=1, le=200), pair=Depends(service)):
        _manager(pair[1])
        return invoke(pair, "list_retention_runs", agent_id=pair[1].agent_id, limit=limit)

    @router.get("/manage/retention/runs/{run_id}")
    def run_detail(run_id: str, pair=Depends(service)):
        client, scope = pair
        _manager(scope)
        record = RetentionStore(client).get_run(namespace_id=scope.namespace_id, run_id=run_id)
        if record is None or (scope.agent_id and record["agent_id"] != scope.agent_id):
            raise HTTPException(404, "Run not found")
        return record

    @router.post("/manage/retention/schedules/preview")
    def preview_schedule(body: PreviewRequest, pair=Depends(service)):
        _manager(pair[1])
        try:
            return preview(body.spec.model_dump(), after=body.after, count=body.count)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/manage/retention/schedules")
    def list_schedules(pair=Depends(service)):
        result = schedule_call(pair, "list")
        if pair[1].agent_id:
            result["items"] = [item for item in result["items"] if item["definition"]["agent_id"] == pair[1].agent_id]
        return result

    def scoped_schedule(pair, schedule_id):
        record = schedule_call(pair, "get", schedule_id)
        if pair[1].agent_id and record["definition"]["agent_id"] != pair[1].agent_id:
            raise HTTPException(404, "Schedule not found")
        return record

    @router.get("/manage/retention/schedules/{schedule_id}")
    def get_schedule(schedule_id: str, pair=Depends(service)):
        return scoped_schedule(pair, schedule_id)

    @router.put("/manage/retention/schedules/{schedule_id}")
    def put_schedule(schedule_id: str, body: ScheduleRequest, pair=Depends(service)):
        actor = _manager(pair[1])
        if pair[1].agent_id and body.definition.agent_id != pair[1].agent_id:
            raise HTTPException(403, "Schedule agent must match the authorized scope")
        if body.expected_revision:
            scoped_schedule(pair, schedule_id)
        return schedule_call(pair, "put", schedule_id, body.definition.model_dump(), actor, body.expected_revision)

    @router.delete("/manage/retention/schedules/{schedule_id}")
    def delete_schedule(schedule_id: str, expected_revision: int = Query(..., ge=1), pair=Depends(service)):
        scoped_schedule(pair, schedule_id)
        return schedule_call(pair, "delete", schedule_id, expected_revision)

    @router.get("/manage/retention/jobs")
    def jobs(limit: int = Query(100, ge=1, le=1000), pair=Depends(service)):
        result = schedule_call(pair, "jobs", limit=limit)
        if pair[1].agent_id:
            result["items"] = [item for item in result["items"] if item["definition"]["agent_id"] == pair[1].agent_id]
        return result

    def scoped_job(pair, job_id):
        # Exact lookup avoids confusing old jobs outside a pagination window with absent jobs.
        client, scope = pair
        _manager(scope)
        store = ScheduleService(client, scope.namespace_id).store
        record = store.get_job(scope.namespace_id, job_id)
        if record is None or (scope.agent_id and record["definition"]["agent_id"] != scope.agent_id):
            raise HTTPException(404, "Job not found")
        return record

    @router.get("/manage/retention/jobs/{job_id}")
    def job_detail(job_id: str, pair=Depends(service)):
        return scoped_job(pair, job_id)

    @router.post("/manage/retention/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, pair=Depends(service)):
        scoped_job(pair, job_id)
        return schedule_call(pair, "cancel", job_id)

    @router.post("/manage/retention/jobs/{job_id}/acknowledge-interrupted")
    def recover_job(job_id: str, body: RecoveryRequest, pair=Depends(service)):
        scoped_job(pair, job_id)
        return schedule_call(pair, "recover", job_id, body.worker_stopped)

    return router
