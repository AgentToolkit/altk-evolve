# Embed the scoped memory REST API

`build_memory_router` lets an integrating FastAPI application reuse Evolve's memory and retention operations. The host supplies the client and authenticated scope through ordinary dependency injection. It is opt-in and separate from the legacy standalone dashboard router.

```python
from fastapi import Depends, FastAPI
from altk_evolve.frontend.api.memory import MemoryScope, build_memory_router

# These dependencies belong to the host application:
# get_evolve_client returns its configured EvolveClient.
# authenticated_principal verifies credentials.
# authorized_service verifies access to the selected service and agent.
def memory_scope(
    principal=Depends(authenticated_principal),
    service=Depends(authorized_service),
):
    return MemoryScope(
        namespace_id=service.id,
        user_id=principal.id,
        agent_id=service.selected_agent_id,  # optional, authorized by the host
        can_manage=principal.can_manage(service),
    )

app = FastAPI()
app.include_router(
    build_memory_router(
        client_dependency=get_evolve_client,
        scope_dependency=memory_scope,
    ),
    prefix="/api/evolve",
)
```

The example assumes host-defined authentication dependencies; request headers or body IDs alone are not authentication. Enforce any feature flag in these dependencies too. Each operation uses the injected client without replacing the process-global MCP client.

| Routes (relative to mount prefix) | Scope |
| --- | --- |
| `/memory/entities`, `/memory/entities/{id}` | Personal inventory/detail, delete, and `/metadata` patch, constrained by namespace, user, and optional agent. |
| `/memory/access` | Record access to a deduplicated list of personally owned entity IDs. |
| `/memory/facts` | Store/retrieve facts for the authenticated namespace/user and optional agent. |
| `/manage/memory/entities` and `/{id}` | Administrative inventory/detail; content and arbitrary metadata are omitted. `/metadata` patch permits title, category, and legal hold. |
| `/manage/retention/policies` and `/{id}` | List and upsert namespace policies. |
| `/manage/retention/runs` and `/{id}` | Run policies and read history, constrained by the authorized agent when supplied. |
| `/manage/retention/schedules` and `/{id}` | Revision-checked schedule CRUD. `/preview` returns upcoming UTC instants. |
| `/manage/retention/jobs` and `/{id}` | Job status; `/{id}/cancel` requests cancellation and `/{id}/acknowledge-interrupted` records confirmed worker failure. |

Administrative routes require `can_manage=True` and a real user identity; that identity becomes the run/schedule actor. Policies are namespace-wide reusable definitions, while schedules and runs can target an agent. Personal routes reject absent and placeholder users, restrict edits to title/category, and reject identity/protection overrides in submitted fact metadata. The namespace and actor come from trusted scope, not request payloads. Detail/inventory reads do not stamp access; call the explicit access route when memories are actually used.

A schedule PUT body contains `definition` and `expected_revision` (zero for creation). See [retention scheduling](retention-scheduling.md) for the definition, worker setup, timing semantics, and recovery. Mounting the router alone does not launch scheduling; attach the runtime to the host lifespan as below. The host can use friendly UI controls to generate cron and display the preview without asking end users to read cron expressions.

MCP tools remain an application integration surface: their namespace, user, and actor arguments must be supplied by a trusted caller. The embedded REST router supplies those values from authenticated host dependencies; it does not make the legacy dashboard or an independently exposed MCP server authenticated.

## Embedded scheduling lifecycle

The `evolve-mcp` launcher already owns the scheduler lifetime. A host embedding only the REST router must attach it to its application lifetime using the same client and storage as the router:

```python
from contextlib import asynccontextmanager
from altk_evolve.retention.scheduler import retention_runtime

@asynccontextmanager
async def lifespan(app):
    with retention_runtime(client):  # the host's shared EvolveClient
        yield

app = FastAPI(lifespan=lifespan)
# Mount build_memory_router with a client dependency returning that same client.
```

The runtime stops and joins its scheduling thread on shutdown. Service replicas coordinate through durable database claims. The retention CLI manages stored resources; it does not start a scheduler in each CLI process.
