import pytest

from altk_forge.converters.sandbox import check_code, run


@pytest.mark.parametrize(
    "code, problem",
    [
        ("import os\ndef convert(t, c): return {}", "import os is not allowed"),
        ("from subprocess import run\ndef convert(t, c): return {}", "from subprocess import is not allowed"),
        ("def convert(t, c): return open('/etc/passwd').read()", "open is not allowed"),
        ("def convert(t, c): return t.__class__", "attribute __class__ is not allowed"),
        ("def convert(t, c): return getattr(t, '_x')", "takes a constant public attribute name"),
        ("def other(t, c): return {}", "no top-level function convert"),
        ("def convert(t, c) return {}", "syntax error"),
    ],
)
def test_check_code_rejects_unsafe_code(code, problem):
    assert any(problem in p for p in check_code(code))


def test_check_code_accepts_allowed_imports():
    assert check_code("import json, re\nfrom collections import Counter\ndef convert(t, c): return {}") == []


def test_run_executes_the_converter_in_a_separate_process():
    code = "import json\ndef convert(trace, context):\n    return {'n': trace['n'] * 2, 'pid': context['p']}"
    results = run(code, [{"trace": {"n": 2}, "context": {"p": "x"}}, {"trace": {}, "context": {}}])
    assert results[0] == {"ok": True, "result": {"n": 4, "pid": "x"}}
    assert results[1]["ok"] is False and "KeyError" in results[1]["error"] and "line 3" in results[1]["error"]


def test_run_blocks_what_the_static_check_misses():
    # A computed attribute name passes check_code() only when it is a constant; at run time getattr() refuses it anyway.
    code = "def convert(trace, context):\n    name = '_' + '_class__'\n    return getattr(trace, name)"
    assert any("constant public attribute" in p for p in check_code(code))
    from altk_forge.converters.run_converter import guarded_import

    with pytest.raises(ImportError):
        guarded_import("os")


def test_run_reports_code_that_fails_to_load():
    results = run("def convert(trace, context):\n    return 1\nraise ValueError('at import')", [{"trace": {}, "context": {}}])
    assert results[0]["ok"] is False and "loading the converter" in results[0]["error"]
