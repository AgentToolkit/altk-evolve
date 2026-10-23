from altk_forge.converters.prompts import code_of, sample_text


def test_code_of_takes_the_largest_python_block():
    answer = "text\n```python\nx = 1\n```\nmore\n```python\ndef convert(t, c):\n    return t\n```"
    assert code_of(answer) == "def convert(t, c):\n    return t\n"
    assert code_of("no code") is None


def test_sample_text_fits_the_budget():
    trace = {"big": "x" * 10_000, "list": list(range(500))}
    assert len(sample_text(trace, budget=2_000)) <= 2_000
