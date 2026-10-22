import pytest

from altk_forge.core.errors import InputError
from altk_forge.pipelines.filters import delete_filter, list_filters, save_filter
from altk_forge.pipelines.integrations import save_integration
from altk_forge.pipelines.manage import save_pipeline


def test_filters(data_dir):
    f = save_filter({"name": "Errors", "criteria": {"from": "2026-01-01", "to": "2026-01-31", "conditions": []}})
    assert f["id"] == "errors"
    assert f["criteria"] == {"from": "2026-01-01T00:00:00.000Z", "to": "2026-01-31T23:59:59.999Z"}
    with pytest.raises(InputError, match="'From' is after 'To'"):
        save_filter({"name": "Bad", "criteria": {"from": "2026-02-01", "to": "2026-01-01"}})
    delete_filter("errors")
    assert list_filters() == []


def test_a_langfuse_input_needs_a_filter(data_dir):
    lf = save_integration({"kind": "langfuse", "host": "http://lf", "public_key": "pk", "secret_key": "sk"})
    with pytest.raises(InputError, match="choose at least one filter"):
        save_pipeline({"name": "x", "inputs": [{"integration_id": lf["id"]}]})
