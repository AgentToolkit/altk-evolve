"""The converter LLM, stubbed: it answers with the code a test gives it, and counts the calls."""

import pytest

from altk_forge.converters import writer
from forge_helpers import answer_with


@pytest.fixture
def llm(monkeypatch, data_dir):
    """chat() stubbed: answers are popped from `llm.answers`; `llm.calls` counts the calls."""

    class Stub:
        answers: list = []
        calls = 0

        def __call__(self, messages):
            Stub.calls += 1
            return answer_with(self.answers.pop(0))

    stub = Stub()
    stub.answers = []
    monkeypatch.setattr(writer, "chat", stub)
    return stub
