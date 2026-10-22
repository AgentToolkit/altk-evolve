"""Routing, without HTTP: which function a method and path reach, and what comes back."""

import pytest

from altk_forge.core.errors import InputError
from altk_forge.web.api import api


def call(method, path, body=None, query=None):
    return api(method, path.strip("/").split("/"), query or {}, lambda: body or {})


def test_unknown_paths_are_none(data_dir):
    assert call("GET", "nothing") is None
    assert call("DELETE", "kinds") is None
    assert call("PATCH", "pipelines") is None


def test_lists_start_empty(data_dir):
    assert call("GET", "integrations") == []
    assert call("GET", "filters") == []
    assert call("GET", "pipelines") == []


def test_create_change_and_delete_a_filter(data_dir):
    made = call("POST", "filters", {"name": "Errors", "criteria": {}})
    assert made["id"] == "errors"
    changed = call("PUT", "filters/errors", {"name": "Errors", "description": "all errors", "criteria": {}})
    assert changed["description"] == "all errors"
    assert call("GET", "filters/errors")["name"] == "Errors"
    assert call("DELETE", "filters/errors") == {"deleted": "errors"}
    with pytest.raises(InputError, match="No filter errors"):
        call("GET", "filters/errors")


def test_pipeline_routes_report_an_unknown_pipeline(data_dir):
    for path in ("pipelines/ghost", "pipelines/ghost/log", "pipelines/ghost/converters", "pipelines/ghost/trajectories"):
        with pytest.raises(InputError, match="No pipeline ghost"):
            call("GET", path)


def test_a_production_pipeline_keeps_its_converters(pipeline):
    call("POST", "pipelines/qa-bot/promote")
    with pytest.raises(InputError, match="keeps its converters"):
        call("DELETE", "pipelines/qa-bot/converters/c1")
    with pytest.raises(InputError, match="sends on its own"):
        call("POST", "pipelines/qa-bot/trajectories/t1/send")


def test_kinds_describe_what_the_console_can_create(data_dir):
    kinds = call("GET", "kinds")
    assert set(kinds) == {"integrations", "filters", "modes", "memory"}
    assert "namespace_mode" in kinds["memory"]["fields"]
