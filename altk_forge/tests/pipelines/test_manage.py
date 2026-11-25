import pytest

from altk_forge.core.errors import InputError
from altk_forge.pipelines.manage import delete_pipeline, list_pipelines, save_pipeline
from altk_forge.pipelines.state import clean_memory, get_pipeline, namespace_settings


def test_save_pipeline_defaults_and_validation(pipeline):
    assert pipeline["id"] == "qa-bot" and pipeline["mode"] == "test"
    assert (pipeline["poll_seconds"], pipeline["settle_seconds"]) == (60, 0)
    assert pipeline["inputs"][0]["filter_ids"] == []  # a folder takes no filters
    with pytest.raises(InputError, match="at least one input"):
        save_pipeline({"name": "x"})
    with pytest.raises(InputError, match="needs an output"):
        save_pipeline({"name": "x", "inputs": pipeline["inputs"], "mode": "production"})


@pytest.mark.parametrize(
    "memory, message",
    [
        ({"namespace_mode": "single"}, "needs its name"),
        ({"namespace_mode": "weird"}, "must be one of"),
        ({"namespace_prefix": "Bad-Name"}, "lowercase letters"),
        ({"trajectory_max_steps": "ten"}, "whole number"),
        ({"profile_revision": "2"}, "needs the profile's name"),
    ],
)
def test_clean_memory_rejects_bad_settings(memory, message):
    with pytest.raises(InputError, match=message):
        clean_memory(memory)


def test_namespace_settings(pipeline):
    assert namespace_settings("qa-bot") == {"namespace": None, "prefix": "qa-bot"}
    save_pipeline({**pipeline, "memory": {"namespace_mode": "single", "namespace_id": "all_of_it"}}, "qa-bot")
    assert namespace_settings("qa-bot") == {"namespace": "all_of_it", "prefix": None}


def test_delete_pipeline(pipeline):
    delete_pipeline("qa-bot")
    assert list_pipelines() == []
    with pytest.raises(InputError, match="No pipeline"):
        get_pipeline("qa-bot")
