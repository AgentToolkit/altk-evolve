"""The runtime is the state the worker, the senders and the API share. If two parts of Forge held different
instances, pausing a pipeline would not stop the worker's run and two sends could run at once."""

import importlib
import pkgutil
import threading
import time

import altk_forge
from altk_forge.pipelines import runtime
from altk_forge.pipelines.runtime import Runtime


def test_every_module_shares_the_one_runtime():
    holders = []
    for info in pkgutil.walk_packages(altk_forge.__path__, "altk_forge."):
        module = importlib.import_module(info.name)
        if hasattr(module, "RUNTIME"):
            holders.append(info.name)
            assert module.RUNTIME is runtime.RUNTIME, f"{info.name} holds a runtime of its own"
    assert {
        "altk_forge.pipelines.state",
        "altk_forge.pipelines.sending",
        "altk_forge.pipelines.manage",
        "altk_forge.pipelines.worker",
    } <= set(holders)


def test_cancel_and_resume():
    rt = Runtime()
    assert not rt.cancelled("p")
    rt.cancel("p")
    assert rt.cancelled("p") and not rt.cancelled("q")
    rt.resume("p")
    assert not rt.cancelled("p")


def test_one_lock_per_pipeline():
    rt = Runtime()
    assert rt.pipeline_lock("p") is rt.pipeline_lock("p")
    assert rt.pipeline_lock("p") is not rt.pipeline_lock("q")


def test_status_is_a_copy_and_status_of_the_live_record():
    rt = Runtime()
    assert rt.status("p") is None
    rt.status_of("p")["state"] = "running"
    copy = rt.status("p")
    copy["state"] = "changed"
    assert rt.status("p") == {"state": "running"}
    rt.set_paused("p")
    assert rt.status("p") == {"state": "paused", "next_poll": None}


def test_schedule_and_wake():
    rt = Runtime()
    assert rt.due("p") == 0
    rt.schedule("p", 100.0)
    rt.schedule("q", 50.0)
    assert rt.due("p") == 100.0
    assert rt.next_due(default=1000.0) == 50.0
    rt.wake("p")  # poll p now
    assert rt.due("p") == 0


def test_wait_returns_as_soon_as_someone_wakes_it():
    rt = Runtime()
    threading.Timer(0.05, rt.wake).start()
    started = time.monotonic()
    rt.wait(10)
    assert time.monotonic() - started < 5
