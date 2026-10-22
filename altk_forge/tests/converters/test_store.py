import pytest

from altk_forge.converters.engine import choose, convert_trace
from altk_forge.converters.errors import ConverterError
from altk_forge.converters.schema import infer_schema
from altk_forge.converters.store import converter_detail, converter_ids, delete_converter, retire
from altk_forge.core.paths import pipe_dir
from forge_helpers import SIMPLE_CONVERTER, qa


def test_retired_and_deleted_converters(llm):
    llm.answers = [SIMPLE_CONVERTER, SIMPLE_CONVERTER]
    convert_trace("p1", qa("t1"), {})
    retire("p1", "c1", "test")
    assert choose("p1", infer_schema(qa("t2"))) == []
    _, _, info = convert_trace("p1", qa("t2"), {})
    assert info["converter"] == "c2"
    delete_converter("p1", "c1")
    assert converter_ids("p1") == ["c2"]
    with pytest.raises(ConverterError):
        delete_converter("p1", "c1")


def test_converter_detail_of_an_unknown_converter(data_dir):
    assert converter_detail("p1", "c9") is None
    assert pipe_dir("p1") == data_dir / "pipelines" / "p1"
