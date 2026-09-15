"""Request-local client injection shared by REST and MCP implementations."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Iterator

from altk_evolve.frontend.client.evolve_client import EvolveClient

injected_client: ContextVar[EvolveClient | None] = ContextVar("evolve_injected_client", default=None)


@contextmanager
def use_client(client: EvolveClient) -> Iterator[None]:
    """Bind a client for this invocation without replacing the MCP singleton."""
    token = injected_client.set(client)
    try:
        yield
    finally:
        injected_client.reset(token)


# A scheduler can stop between entity operations without bypassing protection hooks.
execution_cancelled: ContextVar[Callable[[], bool] | None] = ContextVar("evolve_execution_cancelled", default=None)


def cancellation_requested() -> bool:
    callback = execution_cancelled.get()
    return callback() if callback is not None else False
