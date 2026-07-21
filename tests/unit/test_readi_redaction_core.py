"""Pure core of the READI semantic PII plugin — no cpex, no READI required.

Detection is injected (the ``SpanDetector`` protocol), so the splice/merge
logic that decides what actually gets masked is always-on CI coverage: none of
these tests need the ``[hooks]`` or ``[pii-semantic]`` extras. The cpex shim and a
real NER model are exercised separately in ``test_hooks_plugins.py``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from altk_evolve.hooks.plugins.readi import (
    build_readi_detector,
    redact_entities,
    redact_messages,
    redact_spans,
    redact_text,
)


def fake_detector(*targets: str):
    """A SpanDetector that finds fixed substrings — stands in for an NER model."""

    def detect(text: str) -> list[tuple[int, int]]:
        spans = []
        for target in targets:
            start = text.find(target)
            while start != -1:
                spans.append((start, start + len(target)))
                start = text.find(target, start + 1)
        return spans

    return detect


# ── redact_spans ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_redact_spans_masks_each_span():
    assert redact_spans("Dana called Priya", [(0, 4), (12, 17)]) == "[REDACTED] called [REDACTED]"


@pytest.mark.unit
def test_redact_spans_applies_right_to_left_so_offsets_stay_valid():
    # A left-to-right splice with a longer mask would shift the second span.
    assert redact_spans("ab cd", [(0, 2), (3, 5)], mask="XXXXXXXX") == "XXXXXXXX XXXXXXXX"


@pytest.mark.unit
def test_redact_spans_merges_overlapping_spans_into_one_mask():
    # Two extractors reporting PERSON and NAME over the same words must yield
    # one [REDACTED], not a nested double mask.
    assert redact_spans("Dana Whitfield here", [(0, 14), (5, 14)]) == "[REDACTED] here"


@pytest.mark.unit
def test_redact_spans_merges_adjacent_spans():
    assert redact_spans("abcdef", [(0, 3), (3, 6)]) == "[REDACTED]"


@pytest.mark.unit
def test_redact_spans_custom_mask_and_empty_mask():
    assert redact_spans("hi Dana", [(3, 7)], mask="<X>") == "hi <X>"
    assert redact_spans("hi Dana", [(3, 7)], mask="") == "hi "


@pytest.mark.unit
@pytest.mark.parametrize("spans", [[], [(5, 2)], [(3, 3)]])
def test_redact_spans_ignores_empty_and_inverted_spans(spans):
    assert redact_spans("untouched", spans) == "untouched"


@pytest.mark.unit
def test_redact_spans_clamps_out_of_range_offsets():
    # A detector is untrusted input: clamp rather than raise.
    assert redact_spans("abc", [(-5, 99)]) == "[REDACTED]"


@pytest.mark.unit
@pytest.mark.parametrize("spans", [[(10, 20)], [(-5, -2)], [(99, 100)]])
def test_redact_spans_fully_out_of_range_span_is_a_noop(spans):
    """REGRESSION: a span entirely outside the text must NOT inject a mask.

    Zero-width spans were dropped BEFORE clamping, so a span like (10, 20) on a
    6-char string clamped to (6, 6) and spliced as an INSERTION —
    `redact_spans("abcdef", [(10, 20)])` returned "abcdef[REDACTED]". Clamp then
    drop, and it is a genuine no-op (preserving the None-unchanged contract).
    """
    assert redact_spans("abcdef", spans) == "abcdef"


@pytest.mark.unit
def test_redact_spans_mixed_real_and_out_of_range_redacts_only_the_real_span():
    assert redact_spans("abcdef", [(0, 3), (10, 20)]) == "[REDACTED]def"


@pytest.mark.unit
def test_redact_spans_empty_text():
    assert redact_spans("", [(0, 3)]) == ""


@pytest.mark.unit
def test_redact_spans_uses_character_offsets_on_multibyte_text():
    """REGRESSION: offsets are CHARACTER offsets, never UTF-8 byte offsets.

    cpex-pii-filter's Rust engine reports byte offsets; scoring/splicing those
    as character offsets mis-places every span in a multibyte script (it dragged
    measured Japanese precision from 0.99 to 0.31). READI reports char offsets,
    and this test pins that the core indexes ``str`` accordingly: the name here
    starts at char 5 but byte 15.
    """
    text = "連絡先は山田太郎さんです"
    start, end = text.index("山田太郎"), text.index("山田太郎") + 4
    assert start == 4 and text.encode("utf-8").index("山田太郎".encode()) == 12  # char vs byte
    assert redact_spans(text, [(start, end)]) == "連絡先は[REDACTED]さんです"


# ── redact_text ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_redact_text_runs_the_injected_detector():
    assert redact_text("call Dana now", fake_detector("Dana")) == "call [REDACTED] now"


@pytest.mark.unit
def test_redact_text_empty_string_short_circuits():
    def boom(text: str):
        raise AssertionError("detector must not run on empty text")

    assert redact_text("", boom) == ""


# ── redact_entities ──────────────────────────────────────────────────


@pytest.mark.unit
def test_redact_entities_masks_content():
    result = redact_entities([{"content": "Dana signed off", "type": "note"}], fake_detector("Dana"))
    assert result is not None
    assert result[0] == {"content": "[REDACTED] signed off", "type": "note"}


@pytest.mark.unit
def test_redact_entities_returns_none_when_nothing_detected():
    assert redact_entities([{"content": "nothing here"}], fake_detector("Dana")) is None


@pytest.mark.unit
def test_redact_entities_does_not_mutate_input():
    """Seam contract: changes travel back as a copy, never as in-place mutation."""
    entity = {"content": "Dana", "metadata": {"note": "Dana"}}
    result = redact_entities([entity], fake_detector("Dana"), redact_metadata=True)
    assert result is not None
    assert entity == {"content": "Dana", "metadata": {"note": "Dana"}}
    assert result[0]["metadata"] is not entity["metadata"]


@pytest.mark.unit
def test_redact_entities_redacts_metadata_by_default():
    # Default is redact_metadata=True, matching the regex plugin (which round-
    # trips the whole entity through cpex-pii-filter and so redacts metadata
    # unconditionally). Shipping opposite defaults would be a silent parity gap.
    result = redact_entities([{"content": "x", "metadata": {"owner": "Dana"}}], fake_detector("Dana"))
    assert result is not None
    assert result[0]["metadata"] == {"owner": "[REDACTED]"}


@pytest.mark.unit
def test_redact_entities_can_opt_out_of_metadata_redaction():
    # Opt-out for deployments that key on ids/paths redaction would corrupt.
    result = redact_entities([{"content": "Dana", "metadata": {"owner": "Dana"}}], fake_detector("Dana"), redact_metadata=False)
    assert result is not None
    assert result[0]["metadata"] == {"owner": "Dana"}


@pytest.mark.unit
def test_redact_entities_redacts_metadata_when_enabled():
    result = redact_entities([{"content": "x", "metadata": {"owner": "Dana"}}], fake_detector("Dana"), redact_metadata=True)
    assert result is not None
    assert result[0]["metadata"] == {"owner": "[REDACTED]"}


@pytest.mark.unit
def test_redact_entities_walks_nested_content_structures():
    entity = {"content": {"turns": ["hi Dana", {"note": "Dana again"}], "n": 3, "ok": True}}
    result = redact_entities([entity], fake_detector("Dana"))
    assert result is not None
    assert result[0]["content"] == {"turns": ["hi [REDACTED]", {"note": "[REDACTED] again"}], "n": 3, "ok": True}


@pytest.mark.unit
def test_redact_entities_partial_batch_returns_whole_batch():
    result = redact_entities([{"content": "clean"}, {"content": "Dana"}], fake_detector("Dana"))
    assert result is not None
    assert [e["content"] for e in result] == ["clean", "[REDACTED]"]


@pytest.mark.unit
def test_redact_entities_empty_batch_returns_none():
    assert redact_entities([], fake_detector("Dana")) is None


@pytest.mark.unit
def test_redact_entities_custom_mask():
    result = redact_entities([{"content": "Dana"}], fake_detector("Dana"), mask="***")
    assert result is not None
    assert result[0]["content"] == "***"


# ── redact_messages ──────────────────────────────────────────────────


@pytest.mark.unit
def test_redact_messages_masks_content_and_keeps_role():
    result = redact_messages([{"role": "user", "content": "ask Dana"}], fake_detector("Dana"))
    assert result == [{"role": "user", "content": "ask [REDACTED]"}]


@pytest.mark.unit
def test_redact_messages_returns_none_when_unchanged():
    assert redact_messages([{"role": "user", "content": "hello"}], fake_detector("Dana")) is None


@pytest.mark.unit
def test_redact_messages_does_not_mutate_input():
    message = {"role": "user", "content": "Dana"}
    result = redact_messages([message], fake_detector("Dana"))
    assert result is not None
    assert message == {"role": "user", "content": "Dana"}


@pytest.mark.unit
def test_redact_messages_tolerates_non_string_content():
    # litellm messages may carry structured content blocks or None.
    blocks = [{"role": "user", "content": [{"type": "text", "text": "Dana"}]}, {"role": "tool", "content": None}]
    result = redact_messages(blocks, fake_detector("Dana"))
    assert result is not None
    assert result[0]["content"] == [{"type": "text", "text": "[REDACTED]"}]
    assert result[1]["content"] is None


@pytest.mark.unit
def test_redact_messages_redacts_pii_in_tool_call_arguments():
    """REGRESSION (egress leak): PII in tool_calls[].function.arguments was
    re-sent to the LLM every turn because only `content` was walked. The raw
    arguments string is redacted; ids/role/type/function-name stay intact.
    """
    message = {
        "role": "assistant",
        "id": "msg_1",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "send_email", "arguments": '{"to": "Dana", "body": "hi"}'},
            }
        ],
    }
    result = redact_messages([message], fake_detector("Dana"))
    assert result is not None
    call = result[0]["tool_calls"][0]
    assert "Dana" not in call["function"]["arguments"]
    assert call["function"]["arguments"] == '{"to": "[REDACTED]", "body": "hi"}'
    # Structural fields are never rewritten.
    assert result[0]["role"] == "assistant"
    assert result[0]["id"] == "msg_1"
    assert call["id"] == "call_abc"
    assert call["type"] == "function"
    assert call["function"]["name"] == "send_email"


@pytest.mark.unit
def test_redact_messages_does_not_mutate_input_with_tool_calls():
    message = {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "Dana"}}]}
    result = redact_messages([message], fake_detector("Dana"))
    assert result is not None
    assert message["tool_calls"][0]["function"]["arguments"] == "Dana"  # input untouched
    assert result[0]["tool_calls"][0]["function"]["arguments"] == "[REDACTED]"


@pytest.mark.unit
def test_redact_messages_returns_none_when_only_structural_fields_present():
    # A message with PII only in a structural key (a function name here) must be
    # left unchanged — structural fields are never redacted.
    messages = [{"role": "assistant", "tool_calls": [{"id": "Dana", "type": "function", "function": {"name": "Dana"}}]}]
    assert redact_messages(messages, fake_detector("Dana")) is None


# ── build_readi_detector ─────────────────────────────────────────────


@pytest.mark.unit
def test_build_readi_detector_rejects_unknown_extractor():
    # Validated before any READI import, so this holds without the extra.
    with pytest.raises(ValueError, match="Unknown readi extractor"):
        build_readi_detector(extractor="magic")


@pytest.mark.unit
def test_build_readi_detector_without_readi_names_the_extra():
    if importlib.util.find_spec("risk_assessment") is not None:
        pytest.skip("readi-privacy installed; the degradation path is not active")
    with pytest.raises(ImportError, match=r"altk-evolve\[pii-semantic\]"):
        build_readi_detector()


# ── benchmark helper (byte -> char offsets) ──────────────────────────


def _benchmark_module():
    """Import examples/pii_benchmark.py by path (it is a script, not a package)."""
    path = Path(__file__).resolve().parents[2] / "examples" / "pii_benchmark.py"
    spec = importlib.util.spec_from_file_location("_pii_benchmark", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_benchmark_converts_byte_spans_to_char_spans():
    """REGRESSION: cpex-pii-filter reports BYTE offsets; scoring needs CHAR offsets.

    Without this conversion every regex span on multibyte text lands in the
    wrong place — measured Japanese precision read 0.31 instead of 0.99.
    """
    convert = _benchmark_module().byte_spans_to_char_spans
    text = "連絡先は taro@example.jp です。"
    byte_start = text.encode("utf-8").index(b"taro@example.jp")
    byte_end = byte_start + len("taro@example.jp")
    ((start, end),) = convert(text, [(byte_start, byte_end)])
    assert text[start:end] == "taro@example.jp"
    assert (start, end) != (byte_start, byte_end)  # the conversion is not a no-op here


@pytest.mark.unit
def test_benchmark_byte_span_conversion_is_identity_on_ascii():
    convert = _benchmark_module().byte_spans_to_char_spans
    assert convert("email a@b.com now", [(6, 13)]) == [(6, 13)]


# ── importability without cpex / readi ───────────────────────────────


@pytest.mark.unit
def test_core_usable_and_shim_stub_raises_with_cpex_blocked():
    """The core must import and work where neither cpex nor READI can be imported."""
    code = textwrap.dedent(
        """
        import sys

        class BlockOptionalDeps:
            def find_spec(self, name, path=None, target=None):
                if name.startswith(("cpex", "risk_assessment", "presidio")):
                    # A properly-named ModuleNotFoundError faithfully simulates a
                    # genuinely-absent optional dependency.
                    raise ModuleNotFoundError(f"{name} blocked for this test", name=name)
                return None

        sys.meta_path.insert(0, BlockOptionalDeps())

        from altk_evolve.hooks.plugins.readi import (
            ReadiSemanticPIIPlugin,
            build_readi_detector,
            redact_entities,
            redact_spans,
        )
        from altk_evolve.hooks.types import HAS_CPEX

        assert not HAS_CPEX
        assert redact_spans("Dana here", [(0, 4)]) == "[REDACTED] here"
        assert redact_entities([{"content": "Dana"}], lambda t: [(0, 4)])[0]["content"] == "[REDACTED]"

        try:
            ReadiSemanticPIIPlugin()
        except ImportError as exc:
            assert "altk-evolve[pii-semantic]" in str(exc), str(exc)
        else:
            raise AssertionError("ReadiSemanticPIIPlugin stub did not raise")

        try:
            build_readi_detector()
        except ImportError as exc:
            assert "altk-evolve[pii-semantic]" in str(exc), str(exc)
        else:
            raise AssertionError("build_readi_detector did not raise without READI")
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
