"""REST adapters for the in-process processing manager."""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from altk_evolve.processing import ProcessingError, ProfileConflict, ProfileDefinition, ProfileNotFound, ProfileReference, Trajectory

from altk_evolve.schema.exceptions import NamespaceNotFoundException

router = APIRouter()


def manager():
    from altk_evolve.frontend.mcp.mcp_server import get_client

    return get_client().processing


def _http_error(exc):
    status = 409 if isinstance(exc, ProfileConflict) else 404 if isinstance(exc, (ProfileNotFound, NamespaceNotFoundException)) else 422
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/processors")
def list_processors():
    return manager().registry.inventory()


@router.get("/processing-profiles/{profile_id}")
def get_profile(profile_id: str, response: Response, revision: int | None = None):
    try:
        result = manager().get(profile_id, revision)
    except ProcessingError as exc:
        raise _http_error(exc) from exc
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


@router.put("/processing-profiles/{profile_id}")
def put_profile(
    profile_id: str,
    definition: ProfileDefinition,
    response: Response,
    if_match: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
):
    if if_none_match == "*" and if_match is None:
        expected = 0
    elif if_match and if_none_match is None and if_match.startswith('"') and if_match.endswith('"') and if_match[1:-1].isdigit():
        expected = int(if_match[1:-1])
        if expected < 1:
            raise HTTPException(400, "If-Match must specify a positive revision")
    else:
        raise HTTPException(428, 'Use If-None-Match: * to create, or If-Match: "revision" to update')
    try:
        result = manager().put(profile_id, definition, expected_revision=expected)
    except ProcessingError as exc:
        raise _http_error(exc) from exc
    response.headers["ETag"] = f'"{result["revision"]}"'
    return result


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace_id: str
    trajectory: Trajectory
    processing_profile: ProfileReference


@router.post("/trajectories")
def process_trajectory(request: ProcessRequest) -> dict[str, Any]:
    from altk_evolve.frontend.mcp.mcp_server import get_client

    try:
        result = get_client().process_trajectory(
            request.trajectory, namespace_id=request.namespace_id, processing_profile=request.processing_profile
        )
    except (ProcessingError, NamespaceNotFoundException) as exc:
        raise _http_error(exc) from exc
    return result.model_dump(mode="json")
