import importlib
import os
import uuid
import pytest
from altk_evolve.config.milvus import milvus_client_settings

_EVOLVE_ENV_KEYS = ("EVOLVE_NAMESPACE_ID", "EVOLVE_BACKEND", "EVOLVE_SQLITE_PATH", "EVOLVE_DATA_DIR")


@pytest.fixture(params=["filesystem", "milvus"])
def mcp(request, tmp_path):
    backend_type = request.param

    # Snapshot state before mutating so the finalizer can always restore it
    original_env = {key: os.environ.get(key) for key in _EVOLVE_ENV_KEYS}
    original_milvus_uri = milvus_client_settings.uri

    # Use a per-run namespace to avoid collisions on shared remote backends
    namespace_id = f"test-{uuid.uuid4().hex[:8]}"

    milvus_db_file = None
    evolve_client_ref = [None]

    def _restore():
        import altk_evolve.frontend.mcp.mcp_server as mcp_server_module
        from altk_evolve.config.evolve import evolve_config

        if mcp_server_module._client is not None:
            try:
                mcp_server_module._client.backend.close()
            except Exception:
                pass

        if evolve_client_ref[0] is not None:
            try:
                evolve_client_ref[0].backend.close()
            except Exception:
                pass

        if backend_type == "milvus":
            try:
                from pymilvus import connections

                for alias, _ in connections.list_connections():
                    try:
                        connections.disconnect(alias)
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                from milvus_lite.server_manager import server_manager_instance

                server_manager_instance.release_all()
            except Exception:
                pass

            milvus_client_settings.uri = original_milvus_uri

            for path in [milvus_db_file, f"{milvus_db_file}.lock"] if milvus_db_file else []:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

        for key, original_value in original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value

        # Reset evolve_config singleton to pick up the restored env
        evolve_config.__init__()

        mcp_server_module._client = None
        mcp_server_module._namespace_initialized = False

    # Register finalizer immediately so cleanup runs even if setup raises
    request.addfinalizer(_restore)

    os.environ["EVOLVE_NAMESPACE_ID"] = namespace_id
    os.environ["EVOLVE_BACKEND"] = backend_type
    os.environ["EVOLVE_SQLITE_PATH"] = str(tmp_path / f"test_{backend_type}.sqlite.db")

    if backend_type == "milvus":
        _env_uri = os.getenv("EVOLVE_URI", "")
        if _env_uri.startswith("http"):
            milvus_client_settings.uri = _env_uri
        else:
            milvus_db_file = str(tmp_path / f"test_{uuid.uuid4().hex[:8]}.db")
            milvus_client_settings.uri = milvus_db_file
    elif backend_type == "filesystem":
        os.environ["EVOLVE_DATA_DIR"] = str(tmp_path)

    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.config.evolve import evolve_config

    evolve_config.__init__()

    import altk_evolve.frontend.mcp.mcp_server as mcp_server_module

    mcp_server_module._client = None
    mcp_server_module._namespace_initialized = False

    evolve_client = EvolveClient()
    evolve_client_ref[0] = evolve_client
    try:
        evolve_client.create_namespace(namespace_id)
    except Exception:
        pass

    yield mcp_server_module.mcp
