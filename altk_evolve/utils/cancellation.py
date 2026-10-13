"""Cooperative checkpoints for synchronous service work dispatched by AnyIO."""

from anyio.from_thread import check_cancelled


def check_request_cancelled() -> None:
    """Stop a cancelled worker before further work; ordinary SDK calls have no scope.

    This cannot interrupt a provider request or undo previously committed writes.
    AnyIO documents RuntimeError when called outside one of its worker threads.
    Its cancellation exception propagates unchanged inside a cancelled worker.
    """
    try:
        check_cancelled()
    except RuntimeError:
        pass
