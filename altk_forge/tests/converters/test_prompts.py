import pytest

from altk_forge.converters.prompts import code_of, sample_text


def test_code_of_takes_the_largest_python_block():
    answer = "text\n```python\nx = 1\n```\nmore\n```python\ndef convert(t, c):\n    return t\n```"
    assert code_of(answer) == "def convert(t, c):\n    return t\n"
    assert code_of("no code") is None


def test_sample_text_fits_the_budget():
    trace = {"big": "x" * 10_000, "list": list(range(500))}
    assert len(sample_text(trace, budget=2_000)) <= 2_000


@pytest.mark.parametrize(
    "trace",
    [
        # a trace of Langfuse observations
        {"id": "t", "observations": [{"output": {"content": "a"}}, {"output": "Done."}]},
        # a file from a folder, in the envelope the folder source puts around it: any format at all
        {"id": "f", "name": "run.json", "content": {"steps": [{"result": {"rows": 3}}, {"result": "timeout"}]}},
    ],
)
def test_the_prompt_lists_the_fields_with_more_than_one_type_whatever_the_source(trace):
    from altk_forge.converters.prompts import prompt_extend, prompt_new

    for messages in (prompt_new(trace, "def convert(t, c): ...", "ref"), prompt_extend("code", trace, [])):
        text = messages[1]["content"]
        assert "Fields with more than one type in this trace" in text
        assert "object | string" in text


def test_a_trace_with_one_type_per_field_gets_no_such_section():
    from altk_forge.converters.prompts import prompt_new

    text = prompt_new({"id": "t", "a": "x", "b": [1, 2]}, "def convert(t, c): ...", "ref")[1]["content"]
    assert "Fields with more than one type" not in text


def test_the_contract_is_not_only_for_langfuse():
    from altk_forge.converters.prompts import SYSTEM

    # The general rules come first and name no platform; the Langfuse specifics say they apply to such traces only.
    assert "however the trace records them" in SYSTEM
    assert "Only for a trace of Langfuse observations" in SYSTEM
