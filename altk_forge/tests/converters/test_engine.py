import json

import pytest

from altk_forge.converters.engine import convert_trace
from altk_forge.converters.errors import ConverterError
from altk_forge.converters.store import converter_detail, converter_ids, list_converters, root_of
from altk_forge.converters.writer import ATTEMPTS
from forge_helpers import SIMPLE_CONVERTER, qa


def test_the_first_trace_writes_a_converter_and_the_next_reuses_it(llm):
    llm.answers = [SIMPLE_CONVERTER]
    doc, problems, info = convert_trace("p1", qa("t1"), {})
    assert problems == [] and doc["trace_id"] == "t1"
    assert info == {"converter": "c1", "version": 1, "verdict": "new", "changed": "created"}
    doc, problems, info = convert_trace("p1", qa("t2"), {})
    assert info["verdict"] == "same" and info["changed"] is None
    assert llm.calls == 1  # the same schema needs no LLM call
    meta = json.loads((root_of("p1") / "c1" / "meta.json").read_text())
    assert meta["traces"] == 2
    assert (root_of("p1") / "c1" / "samples" / "t1.trace.json").exists()


def test_a_wider_trace_extends_the_converter(llm):
    llm.answers = [SIMPLE_CONVERTER, SIMPLE_CONVERTER]
    convert_trace("p1", qa("t1"), {})
    doc, problems, info = convert_trace("p1", qa("t2", tags=["new"]), {})
    assert info == {"converter": "c1", "version": 2, "verdict": "wider", "changed": "extended"}
    detail = converter_detail("p1", "c1")
    assert detail["version"] == 2 and detail["diff"] is not None
    assert "tags" in detail["schema"]["properties"]
    assert sorted(detail["samples"]) == ["t1", "t2"]


def test_a_different_trace_gets_a_converter_of_its_own(llm):
    llm.answers = [SIMPLE_CONVERTER, SIMPLE_CONVERTER]
    convert_trace("p1", qa("t1"), {})
    _, _, info = convert_trace("p1", {"id": "t2", "question": "Q?", "answer": 42}, {})
    assert info["converter"] == "c2" and info["verdict"] == "different"
    assert [c["id"] for c in list_converters("p1")] == ["c1", "c2"]


def test_failed_attempts_are_fed_back_until_one_passes(llm):
    llm.answers = ["import os\ndef convert(t, c): return {}", SIMPLE_CONVERTER]
    logged = []
    _, problems, info = convert_trace("p1", qa("t1"), {}, log=lambda line, *_: line and logged.append(line))
    assert problems == [] and info["converter"] == "c1"
    assert llm.calls == 2
    assert any("attempt 1/3 rejected" in line for line in logged)
    history = converter_detail("p1", "c1")["history"]
    assert history[0]["attempts"] == 2


def test_no_working_converter_after_every_attempt(llm):
    llm.answers = ["def convert(t, c): return {}"] * ATTEMPTS
    with pytest.raises(ConverterError, match="no working converter"):
        convert_trace("p1", qa("t1"), {})
    assert converter_ids("p1") == []


def test_may_write_false_never_calls_the_llm(llm):
    with pytest.raises(ConverterError, match="needs a new or extended converter"):
        convert_trace("p1", qa("t1"), {}, may_write=False)
    assert llm.calls == 0
