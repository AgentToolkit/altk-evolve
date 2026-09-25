"""Tests for the consistency analyzer's k-sample resampling.

The contract under test: asking for k samples yields k samples regardless of whether
the platform/model pair honours the `n` completion-count parameter. Providers fail on
n>1 in three different ways (litellm refuses pre-flight, the API returns a 400, or the
gateway ignores `n` and returns one choice), so the fast path is only ever a fast path.
"""

from unittest.mock import Mock, patch

import pytest
from litellm.exceptions import UnsupportedParamsError

from altk_evolve.config.guidelines import guidelines_settings
from altk_evolve.llm.guidelines.consistency_analyzer import inference_utils
from altk_evolve.llm.guidelines.consistency_analyzer.resampling import extract_raw_samples
from altk_evolve.schema.exceptions import EvolveException


@pytest.fixture(autouse=True)
def clear_n_support_cache():
    """The n>1 verdict is cached process-wide; don't let one test's verdict leak."""
    inference_utils.reset_n_support_cache()
    yield
    inference_utils.reset_n_support_cache()


def _response(count: int) -> Mock:
    """A completion response carrying `count` choices."""
    response = Mock()
    response.choices = [Mock(name=f"choice{i}") for i in range(count)]
    return response


def _unsupported() -> UnsupportedParamsError:
    return UnsupportedParamsError(message="n is not supported", model="anthropic/claude", llm_provider="anthropic")


def _sample(**overrides):
    kwargs = dict(prompt="trajectory step", model_id="gpt-4o", temperature=0.5, samples=5)
    kwargs.update(overrides)
    return inference_utils.get_response_sampling(**kwargs)


# ── the batched fast path ────────────────────────────────────────────


@pytest.mark.unit
def test_batched_path_used_when_n_supported():
    """A capable provider still costs exactly one call, with n=k on it."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n", "temperature"]):
        with patch.object(inference_utils, "completion", return_value=_response(5)) as mock_completion:
            choices = _sample()

    assert len(choices) == 5
    assert mock_completion.call_count == 1
    assert mock_completion.call_args.kwargs["n"] == 5


@pytest.mark.unit
def test_single_sample_never_asks_for_n():
    """samples=1 is a legitimate request: one call, no `n`, and no 'need 2 samples' raise.

    The old implementation raised whenever it got fewer than two choices, so asking
    for one sample was an unconditional error.
    """
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            choices = _sample(samples=1)

    assert len(choices) == 1
    assert mock_completion.call_count == 1
    assert "n" not in mock_completion.call_args.kwargs


# ── falling back to a loop ───────────────────────────────────────────


@pytest.mark.unit
def test_static_probe_skips_batched_call_entirely():
    """When litellm already knows `n` is unsupported, don't waste a call discovering it."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["temperature", "max_tokens"]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            choices = _sample(model_id="anthropic/claude-sonnet-4-20250514")

    assert len(choices) == 5
    assert mock_completion.call_count == 5
    assert all("n" not in call.kwargs for call in mock_completion.call_args_list)


@pytest.mark.unit
@pytest.mark.parametrize(
    "model_id,provider",
    [
        ("groq/llama-3.3-70b-versatile", None),
        ("llama-3.3-70b", "groq"),
    ],
)
def test_groq_loops_despite_advertised_n_support(model_id, provider):
    """Groq lists `n` in its supported params but rejects any value above 1."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            choices = _sample(model_id=model_id, custom_llm_provider=provider)

    assert len(choices) == 5
    assert mock_completion.call_count == 5


@pytest.mark.unit
def test_unsupported_params_error_falls_back_and_is_remembered():
    """One wasted call per process, not per resampled step."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion") as mock_completion:
            mock_completion.side_effect = [_unsupported()] + [_response(1)] * 5
            first = _sample(model_id="anthropic/claude")

            assert len(first) == 5
            assert mock_completion.call_count == 6  # 1 rejected batch + 5 loop calls

            mock_completion.reset_mock()
            mock_completion.side_effect = [_response(1)] * 5
            second = _sample(model_id="anthropic/claude")

    assert len(second) == 5
    assert mock_completion.call_count == 5, "the batched attempt should not be retried once known to fail"


@pytest.mark.unit
def test_transient_failure_does_not_poison_the_capability_cache():
    """A timeout says nothing about n>1 support, so the cheap path stays available."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion") as mock_completion:
            # Both batched attempts time out, the loop fills in, then a second
            # request finds the batched path still enabled.
            mock_completion.side_effect = [RuntimeError("timeout"), RuntimeError("timeout")] + [_response(1)] * 5
            first = _sample()
            assert len(first) == 5
            assert mock_completion.call_count == 7  # 2 batched attempts + 5 loop calls

            mock_completion.reset_mock()
            mock_completion.side_effect = None
            mock_completion.return_value = _response(5)
            second = _sample()

    assert len(second) == 5
    assert mock_completion.call_count == 1
    assert mock_completion.call_args.kwargs["n"] == 5


@pytest.mark.unit
def test_short_batched_return_is_topped_up():
    """A gateway that accepts `n` and ignores it: keep the 2 choices, fetch the other 3."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion") as mock_completion:
            mock_completion.side_effect = [_response(2)] + [_response(1)] * 3
            choices = _sample()

    assert len(choices) == 5
    assert mock_completion.call_count == 4
    assert mock_completion.call_args_list[0].kwargs["n"] == 5
    assert all("n" not in call.kwargs for call in mock_completion.call_args_list[1:])


# ── partial results ──────────────────────────────────────────────────


@pytest.mark.unit
def test_partial_loop_results_are_accepted_with_a_warning(caplog):
    """Two flaky samples shouldn't cost the whole trajectory; three samples still score."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion") as mock_completion:
            # 3 calls succeed, 2 fail every one of their retries.
            def side_effect(**kwargs):
                side_effect.calls += 1
                if side_effect.calls <= 3:
                    return _response(1)
                raise RuntimeError("boom")

            side_effect.calls = 0
            mock_completion.side_effect = side_effect

            with caplog.at_level("WARNING"):
                choices = _sample()

    assert len(choices) == 3
    assert "obtained 3" in caplog.text


@pytest.mark.unit
def test_raises_when_fewer_than_two_samples_survive():
    """Below two samples there is no variance to measure, so surface a real error."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", side_effect=RuntimeError("boom")):
            with pytest.raises(EvolveException, match="only obtained 0"):
                _sample()


# ── concurrency ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_loop_is_bounded_by_configured_workers(monkeypatch):
    """max_workers=1 runs in-line — no thread pool — and preserves submission order."""
    import threading

    monkeypatch.setattr(guidelines_settings, "consistency_resample_max_workers", 1)
    main_thread = threading.current_thread().name
    threads: list[str] = []
    ordered = [_response(1) for _ in range(5)]

    def side_effect(**kwargs):
        threads.append(threading.current_thread().name)
        return ordered[len(threads) - 1]

    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", side_effect=side_effect):
            choices = _sample()

    assert threads == [main_thread] * 5
    assert choices == [r.choices[0] for r in ordered], "results must come back in submission order"


@pytest.mark.unit
def test_loop_results_keep_submission_order_when_parallel(monkeypatch):
    """Debug artifacts should be reproducible, so order must not follow completion time.

    Each call sleeps for a duration that decreases with its submission index, so the
    calls finish in roughly reverse order. Returning them in submission order anyway
    is what this asserts.
    """
    import threading
    import time

    monkeypatch.setattr(guidelines_settings, "consistency_resample_max_workers", 5)
    lock = threading.Lock()
    issued = {"count": 0}

    def side_effect(**kwargs):
        with lock:
            index = issued["count"]
            issued["count"] += 1
        time.sleep((5 - index) * 0.02)
        response = Mock()
        choice = Mock()
        choice.submission_index = index
        response.choices = [choice]
        return response

    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", side_effect=side_effect):
            choices = _sample()

    assert [c.submission_index for c in choices] == [0, 1, 2, 3, 4]


# ── redaction seam ───────────────────────────────────────────────────


@pytest.mark.unit
def test_pre_call_hook_fires_once_across_the_whole_loop():
    """k fallback calls must not mean k dispatches — the raw trajectory is sent once
    to the hook, and every call re-sends the same redacted messages."""
    from altk_evolve.llm.guidelines.consistency_analyzer import inference_utils as iu

    with patch.object(iu, "dispatch_llm_pre_call", return_value=[{"role": "user", "content": "REDACTED"}]) as mock_hook:
        with patch.object(iu, "get_supported_openai_params", return_value=[]):
            with patch.object(iu, "completion", return_value=_response(1)) as mock_completion:
                _sample()

    assert mock_hook.call_count == 1
    assert mock_completion.call_count == 5
    for call in mock_completion.call_args_list:
        assert call.kwargs["messages"] == [{"role": "user", "content": "REDACTED"}]


@pytest.mark.unit
def test_parallel_calls_do_not_share_one_messages_list():
    """litellm rewrites messages in place on some provider paths, so k concurrent
    calls must each get their own list and their own message dicts."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            _sample()

    sent = [call.kwargs["messages"] for call in mock_completion.call_args_list]
    assert len({id(m) for m in sent}) == 5, "each call needs its own messages list"
    assert len({id(m[0]) for m in sent}) == 5, "each call needs its own message dicts"


# ── samples that record no decision ──────────────────────────────────


def _text_choice(content, finish_reason="stop", reasoning=None):
    """A choice as litellm returns one for a text response."""
    choice = Mock()
    choice.finish_reason = finish_reason
    choice.message.tool_calls = None
    choice.message.content = content
    choice.message.reasoning_content = reasoning
    return choice


def _tool_call_choice(name="get_weather", args='{"city": "Paris"}'):
    """A *successful* tool-call response — note content is legitimately empty."""
    call = Mock()
    call.model_dump.return_value = {"function": {"name": name, "arguments": args}, "type": "function"}
    choice = Mock()
    choice.finish_reason = "tool_calls"
    choice.message.tool_calls = [call]
    choice.message.content = ""
    choice.message.reasoning_content = None
    return choice


@pytest.mark.unit
def test_truncated_samples_are_dropped():
    """A reasoning model that runs out of budget returns finish_reason='length' with
    content='' — a successful response carrying no decision."""
    choices = [_text_choice("", "length", reasoning="The user") for _ in range(5)]

    assert extract_raw_samples(choices) == {"num_samples": 0, "raw_samples": []}


@pytest.mark.unit
def test_successful_tool_call_response_is_kept_despite_empty_content():
    """The regression this fix must not cause: a tool-call response has content='' too,
    so keying on blank content alone would discard every tool-call sample."""
    result = extract_raw_samples([_tool_call_choice() for _ in range(3)])

    assert result["num_samples"] == 3
    assert result["raw_samples"][0] == [{"function": {"name": "get_weather", "arguments": '{"city": "Paris"}'}, "type": "function"}]


@pytest.mark.unit
def test_only_the_empty_samples_are_dropped():
    """Partial truncation scores on the survivors rather than throwing the step away."""
    choices = [
        _text_choice("Book the flight."),
        _text_choice("", "length"),
        _text_choice("Cancel the order."),
        _text_choice("   \n ", "length"),
        _text_choice("", "length"),
    ]

    result = extract_raw_samples(choices)

    assert result["num_samples"] == 2
    assert result["raw_samples"] == ["Book the flight.", "Cancel the order."]


@pytest.mark.unit
def test_warning_names_the_truncation_cause(caplog):
    """The log has to explain *why* the step lost its samples, or an operator sees only
    a thinner score card with no cause."""
    choices = [_text_choice("Book it."), _text_choice("", "length", reasoning="We need to")]

    with caplog.at_level("WARNING"):
        extract_raw_samples(choices)

    assert "Discarded 1 of 2" in caplog.text
    assert "finish_reason='length'" in caplog.text
    assert "reasoning_content" in caplog.text


@pytest.mark.unit
def test_no_warning_when_every_sample_is_usable(caplog):
    with caplog.at_level("WARNING"):
        result = extract_raw_samples([_text_choice("Book it.")] * 3)

    assert result["num_samples"] == 3
    assert caplog.text == ""


@pytest.mark.unit
def test_none_content_still_skipped_without_being_counted_as_truncated():
    """Pre-existing behaviour: a None content was always skipped."""
    result = extract_raw_samples([_text_choice(None), _text_choice("Book it.")])

    assert result == {"num_samples": 1, "raw_samples": ["Book it."]}


@pytest.mark.unit
def test_dict_shaped_choices_are_handled():
    """Serialized/cached choices take the dict branch, which keys on tool_calls presence."""
    choices = [
        {"finish_reason": "stop", "message": {"content": "Book it."}},
        {"finish_reason": "length", "message": {"content": ""}},
        {"finish_reason": "tool_calls", "message": {"tool_calls": [{"function": {"name": "f"}}]}},
        {"finish_reason": "length", "message": {"tool_calls": []}},
    ]

    result = extract_raw_samples(choices)

    assert result["num_samples"] == 2
    assert result["raw_samples"] == ["Book it.", [{"function": {"name": "f"}}]]


@pytest.mark.unit
def test_fully_truncated_step_scores_as_undefined_not_as_consistent():
    """End of the chain: an all-truncated step must be excluded from the score card,
    not reported as perfectly consistent with uncertainty 0.0."""
    from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

    config = {
        "max_samples": 5,
        "aggregation": "mean",
        "agents": [{"name": "OpenAIAgent_content", "response_type": "text", "metric": "jaccard"}],
    }
    truncated = [_text_choice("", "length") for _ in range(5)]
    divergent = [_text_choice(t) for t in ("Book the flight.", "Cancel it.", "Email Bob.", "Search hotels.", "Wait.")]
    trajectory = {
        "task": "t",
        "steps": [
            {"name": "OpenAIAgent_content", "step_number": 0, "sampling": extract_raw_samples(truncated)},
            {"name": "OpenAIAgent_content", "step_number": 1, "sampling": extract_raw_samples(divergent)},
        ],
    }

    card, scored = analyze_consistency(trajectory=trajectory, config=config)

    assert scored["steps"][0]["consistency"]["step_consistency"] == -1, "truncated step must be undefined, not 1.0"
    # Only the step with real samples reaches the score card; the truncated one is
    # excluded rather than contributing uncertainty 0.0 and dragging the trajectory
    # towards "nothing looked uncertain".
    assert [s["step_number"] for s in card["steps"]] == [1]
    assert card["steps"][0]["step_uncertainty"] > 0


# ── the loop-route sample ceiling ─────────────────────────────────────


@pytest.mark.unit
def test_loop_route_caps_samples_regardless_of_config():
    """Each loop call re-bills the trajectory prompt, so a config asking for 20 gets 5."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            choices = _sample(samples=20)

    assert len(choices) == inference_utils.MAX_LOOP_SAMPLES == 5
    assert mock_completion.call_count == 5, "must not issue 20 calls"


@pytest.mark.unit
def test_batched_route_is_not_capped():
    """n=k shares one input billing across all k, so a capable provider still gets 20."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion", return_value=_response(20)) as mock_completion:
            choices = _sample(samples=20)

    assert len(choices) == 20
    assert mock_completion.call_count == 1
    assert mock_completion.call_args.kwargs["n"] == 20


@pytest.mark.unit
def test_batched_choices_above_the_cap_are_kept():
    """7 choices already returned cost one prompt between them — don't throw 2 away."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion", return_value=_response(7)) as mock_completion:
            choices = _sample(samples=20)

    assert len(choices) == 7
    assert mock_completion.call_count == 1, "no top-up once the cap is already met"


@pytest.mark.unit
def test_top_up_stops_at_the_cap():
    """Batched gave 2 of 20; top up to the ceiling, not to 20."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=["n"]):
        with patch.object(inference_utils, "completion") as mock_completion:
            mock_completion.side_effect = [_response(2)] + [_response(1)] * 3
            choices = _sample(samples=20)

    assert len(choices) == 5
    assert mock_completion.call_count == 4  # 1 batched + 3 top-up


@pytest.mark.unit
def test_config_below_the_cap_is_untouched():
    """The ceiling is a maximum, not a target — asking for 3 still gets exactly 3."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", return_value=_response(1)) as mock_completion:
            choices = _sample(samples=3)

    assert len(choices) == 3
    assert mock_completion.call_count == 3


@pytest.mark.unit
def test_cap_is_announced_once_per_model_not_once_per_step(caplog):
    """Resampling calls this per step; 15 identical INFO lines per trajectory is noise."""
    with patch.object(inference_utils, "get_supported_openai_params", return_value=[]):
        with patch.object(inference_utils, "completion", return_value=_response(1)):
            with caplog.at_level("INFO"):
                _sample(samples=20)
                _sample(samples=20)
                _sample(samples=20, model_id="other-model")

    capping = [r for r in caplog.records if "capping the configured" in r.message and r.levelname == "INFO"]
    assert len(capping) == 2, "one INFO per model, later steps demoted to DEBUG"
    assert {r.message.split()[0] for r in capping} == {"gpt-4o", "other-model"}


@pytest.mark.unit
def test_capped_sample_count_does_not_break_tool_call_scoring():
    """The cap leaves a step holding fewer samples than config asked for. The
    MIN_FRACTION field-presence threshold must track the samples actually obtained,
    or every field is skipped and the step scores undefined."""
    from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency

    parsed = [{"function_name": "get_weather", "function_arguments": '{"city": "Paris"}'} for _ in range(5)]
    parsed[4]["function_name"] = "get_forecast"
    agents = [
        {
            "name": "OpenAIAgent_tool_calls",
            "response_type": "tool_calls",
            "fields": [{"name": "function_name", "metric": "jaccard"}, {"name": "function_arguments", "metric": "jaccard"}],
        }
    ]

    for config_max in (5, 10, 20):
        trajectory = {
            "steps": [
                {
                    "name": "OpenAIAgent_tool_calls",
                    "sampling": {"num_samples": 5, "raw_samples": [1] * 5, "parsed_samples": parsed},
                }
            ]
        }
        scored = compute_step_consistency(trajectory, {"max_samples": config_max, "aggregation": "mean", "agents": agents})
        consistency = scored["steps"][0]["consistency"]["step_consistency"]
        assert consistency > 0, f"config max_samples={config_max} with 5 real samples scored {consistency}"
