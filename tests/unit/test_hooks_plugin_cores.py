"""Pure plugin cores (normalizer, access stamp) — no cpex required.

These are the always-on CI coverage for the plugins' domain logic: unlike
``test_hooks_plugins.py`` (which exercises the cpex shims end-to-end and
importorskips cpex), nothing here needs the ``[hooks]`` extra. The last test
proves it by importing and using the cores in a subprocess where cpex imports
are blocked.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import textwrap

import pytest

from altk_evolve.hooks.plugins.access_stamp import build_access_stamps
from altk_evolve.hooks.plugins.normalizer import normalize_entities

FROZEN = datetime.datetime(2026, 7, 8, 12, 0, 0, tzinfo=datetime.UTC)


def frozen_now() -> datetime.datetime:
    return FROZEN


# ── normalize_entities ───────────────────────────────────────────────


@pytest.mark.unit
def test_normalize_copies_task_id_to_trace_id():
    result = normalize_entities([{"content": "x", "metadata": {"task_id": "t-42", "created_at": "2020"}}])
    assert result is not None
    assert result[0]["metadata"]["trace_id"] == "t-42"
    assert result[0]["metadata"]["task_id"] == "t-42"


@pytest.mark.unit
def test_normalize_stamps_created_at_with_injected_clock():
    result = normalize_entities([{"metadata": {"trace_id": "tr"}}], now=frozen_now)
    assert result is not None
    assert result[0]["metadata"]["created_at"] == "2026-07-08T12:00:00+00:00"


@pytest.mark.unit
def test_normalize_preserves_existing_trace_id_and_created_at():
    metadata = {"task_id": "t-1", "trace_id": "existing", "created_at": "2020-01-01T00:00:00+00:00"}
    assert normalize_entities([{"metadata": metadata}]) is None  # nothing to do -> unchanged


@pytest.mark.unit
def test_normalize_no_trace_id_stamp_without_task_id():
    result = normalize_entities([{"metadata": {}}], now=frozen_now)
    assert result is not None
    assert "trace_id" not in result[0]["metadata"]  # only created_at was stamped
    assert result[0]["metadata"] == {"created_at": FROZEN.isoformat()}


@pytest.mark.unit
def test_normalize_handles_missing_and_none_metadata():
    result = normalize_entities([{"content": "a"}, {"content": "b", "metadata": None}], now=frozen_now)
    assert result is not None
    assert all(e["metadata"] == {"created_at": FROZEN.isoformat()} for e in result)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stamp_trace_id", "stamp_created_at", "expected"),
    [
        (True, True, {"task_id": "t", "trace_id": "t", "created_at": FROZEN.isoformat()}),
        (True, False, {"task_id": "t", "trace_id": "t"}),
        (False, True, {"task_id": "t", "created_at": FROZEN.isoformat()}),
    ],
)
def test_normalize_flag_matrix(stamp_trace_id: bool, stamp_created_at: bool, expected: dict):
    result = normalize_entities(
        [{"metadata": {"task_id": "t"}}],
        stamp_trace_id=stamp_trace_id,
        stamp_created_at=stamp_created_at,
        now=frozen_now,
    )
    assert result is not None
    assert result[0]["metadata"] == expected


@pytest.mark.unit
def test_normalize_both_flags_off_returns_none():
    assert normalize_entities([{"metadata": {"task_id": "t"}}], stamp_trace_id=False, stamp_created_at=False) is None


@pytest.mark.unit
def test_normalize_empty_batch_returns_none():
    assert normalize_entities([]) is None


@pytest.mark.unit
def test_normalize_does_not_mutate_input():
    entity = {"content": "x", "metadata": {"task_id": "t"}}
    result = normalize_entities([entity], now=frozen_now)
    assert result is not None
    assert entity == {"content": "x", "metadata": {"task_id": "t"}}  # input untouched
    assert result[0] is not entity


@pytest.mark.unit
def test_normalize_partial_batch_change_returns_full_batch():
    # One entity already normalized, one not -> the whole batch comes back.
    done = {"metadata": {"trace_id": "tr", "created_at": "2020"}}
    result = normalize_entities([done, {"metadata": {"task_id": "t"}}], now=frozen_now)
    assert result is not None
    assert len(result) == 2
    assert result[0]["metadata"] == done["metadata"]


# ── build_access_stamps ──────────────────────────────────────────────


@pytest.mark.unit
def test_access_stamps_shared_deterministic_timestamp():
    stamps = build_access_stamps([{"id": "a"}, {"id": "b"}], now=frozen_now)
    assert stamps == [
        ("a", {"last_accessed": "2026-07-08T12:00:00+00:00"}),
        ("b", {"last_accessed": "2026-07-08T12:00:00+00:00"}),
    ]


@pytest.mark.unit
def test_access_stamps_skip_entities_without_id():
    stamps = build_access_stamps([{"id": ""}, {"id": None}, {"content": "no id"}, {"id": "keep"}], now=frozen_now)
    assert [entity_id for entity_id, _ in stamps] == ["keep"]


@pytest.mark.unit
def test_access_stamps_coerce_ids_to_str():
    stamps = build_access_stamps([{"id": 42}], now=frozen_now)
    assert stamps[0][0] == "42"


@pytest.mark.unit
def test_access_stamps_empty_batch():
    assert build_access_stamps([], now=frozen_now) == []


# ── importability without cpex ───────────────────────────────────────


@pytest.mark.unit
def test_cores_usable_and_stubs_raise_with_cpex_blocked():
    """The cores must import and work in a process where cpex cannot be imported.

    cpex may already be imported in this test process, so the blocker runs in a
    subprocess: a meta_path finder rejects any ``cpex*`` import before
    altk_evolve is loaded.
    """
    code = textwrap.dedent(
        """
        import sys

        class BlockCpex:
            def find_spec(self, name, path=None, target=None):
                if name == "cpex" or name.startswith(("cpex.", "cpex_")):
                    # Raise a properly-named ModuleNotFoundError to faithfully
                    # simulate a genuinely-absent optional dependency. pii.py's
                    # import guard falls back to its stub only for a
                    # ModuleNotFoundError naming cpex/cpex_pii_filter; a
                    # name-less ImportError would (correctly) propagate.
                    raise ModuleNotFoundError(f"{name} blocked for this test", name=name)
                return None

        sys.meta_path.insert(0, BlockCpex())

        from altk_evolve.hooks.plugins.access_stamp import AccessStampPlugin, build_access_stamps
        from altk_evolve.hooks.plugins.normalizer import MetadataNormalizerPlugin, normalize_entities
        from altk_evolve.hooks.types import HAS_CPEX

        assert not HAS_CPEX
        assert normalize_entities([{"metadata": {"task_id": "t"}}])[0]["metadata"]["trace_id"] == "t"
        assert build_access_stamps([{"id": "e1"}])[0][0] == "e1"

        for stub in (MetadataNormalizerPlugin, AccessStampPlugin):
            try:
                stub()
            except ImportError as exc:
                assert "altk-evolve[hooks]" in str(exc), str(exc)
            else:
                raise AssertionError(f"{stub.__name__} stub did not raise")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
