import anyio
import pytest

from altk_evolve.frontend.mcp.http_transport import _is_benign_disconnect_exception

pytestmark = pytest.mark.unit


def test_closed_resource_disconnect_is_benign() -> None:
    assert _is_benign_disconnect_exception(anyio.ClosedResourceError()) is True


def test_nested_disconnect_group_is_benign() -> None:
    exc = ExceptionGroup(
        "disconnect",
        [
            anyio.ClosedResourceError(),
            ExceptionGroup("nested", [anyio.BrokenResourceError()]),
        ],
    )

    assert _is_benign_disconnect_exception(exc) is True


def test_mixed_exception_group_is_not_benign() -> None:
    exc = ExceptionGroup(
        "mixed",
        [anyio.ClosedResourceError(), RuntimeError("real failure")],
    )

    assert _is_benign_disconnect_exception(exc) is False
