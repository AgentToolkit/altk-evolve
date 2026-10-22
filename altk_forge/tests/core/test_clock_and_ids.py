import pytest

from altk_forge.core.clock import utc
from altk_forge.core.errors import InputError
from altk_forge.core.ids import unique_id
from altk_forge.core.paths import pipe_dir


def test_utc_and_ids():
    assert utc("2026-01-02") == "2026-01-02T00:00:00.000Z"
    assert utc("2026-01-02T10:00:00+02:00") == "2026-01-02T08:00:00.000Z"
    with pytest.raises(InputError):
        utc("yesterday")
    assert unique_id("My Pipe!", {"my-pipe"}) == "my-pipe-2"
    with pytest.raises(InputError, match="Invalid pipeline id"):
        pipe_dir("../escape")
