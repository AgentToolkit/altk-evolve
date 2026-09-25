"""
LLM inference utilities for the consistency analyzer.

Replaces the original IBM-specific provider dispatch (RITS, WatsonX, IBM LiteLLM)
with a single litellm.completion() call, matching the pattern used throughout the
rest of altk-evolve (see altk_evolve/llm/guidelines/guidelines.py).

Consistency scoring needs k independent samples per step. The cheap way to get them
is one call with n=k (the trajectory prompt is billed as input once instead of k
times), but not every platform/model combination accepts `n`, in three distinct
shapes:

  * litellm refuses the request before any HTTP call (anthropic, ollama, bedrock —
    `n` is absent from get_supported_openai_params and litellm.drop_params is False);
  * the provider's API rejects n>1 with a 400 even though litellm advertises support
    (groq);
  * an OpenAI-compatible gateway accepts `n` and quietly returns a single choice.

get_response_sampling() therefore treats n=k as a fast path only, and falls back to
k separate single-completion calls — bounded-parallel, topping up whatever the
batched attempt produced — so that asking for k samples yields k samples anywhere.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from litellm import completion, get_supported_openai_params
from litellm.exceptions import BadRequestError, UnsupportedParamsError

from altk_evolve.config.guidelines import guidelines_settings
from altk_evolve.hooks.manager import dispatch_llm_pre_call
from altk_evolve.schema.exceptions import EvolveException

logger = logging.getLogger(__name__)

MAX_NEW_TOKENS = 3000

# Consistency scoring is a variance measurement, so it is meaningless below two
# samples — but a caller that explicitly asks for one sample gets one, no error.
MIN_USABLE_SAMPLES = 2

# Hard ceiling on samples fetched via the fallback loop, overriding max_samples from
# the config. On the batched route k samples share one input billing of the trajectory
# prompt; on the loop route each sample pays for it again, so a config tuned for the
# cheap route becomes k times as expensive on a provider that won't honour `n`. The
# batched route stays uncapped.
MAX_LOOP_SAMPLES = 5

# Retry budgets. The batched attempt is cheap to abandon (the loop path is right
# behind it); an individual loop call is the last chance at that sample.
_BATCHED_ATTEMPTS = 2
_SINGLE_ATTEMPTS = 3

# Providers that advertise `n` through get_supported_openai_params but reject n>1 at
# the API. Same special-casing as the constrained-decoding checks in guidelines.py,
# clustering.py and consistency_guidelines.py.
_PROVIDERS_WITHOUT_N = frozenset({"groq"})

# (model_id, custom_llm_provider) -> whether n>1 is usable. Populated by the static
# probe and corrected by runtime evidence, so a provider that rejects `n` costs one
# wasted call per process rather than one per resampled step.
_N_SUPPORT: dict[tuple[str, str | None], bool] = {}


# (model_id, provider) pairs whose loop-route cap has been announced. Resampling calls
# get_response_sampling once per step, so without this the same cap notice would repeat
# for every step of every trajectory in a sync.
_CAP_LOGGED: set[tuple[str, str | None]] = set()


def reset_n_support_cache() -> None:
    """Forget everything learned about n>1 support. For tests and settings reloads."""
    _N_SUPPORT.clear()
    _CAP_LOGGED.clear()


def _log_loop_cap(model_id: str, provider: str | None, samples: int, target: int) -> None:
    """Announce the loop-route cap at INFO once per model, DEBUG for later steps."""
    message = (
        f"{model_id} needs one call per sample, which bills the trajectory prompt each time; "
        f"capping the configured {samples} samples at {target} for this step."
    )
    key = (model_id, provider)
    if key in _CAP_LOGGED:
        logger.debug(message)
        return
    _CAP_LOGGED.add(key)
    logger.info(message)


def _supports_n(model_id: str, custom_llm_provider: str | None) -> bool:
    """Best guess at whether this model/provider pair will honour n>1.

    Optimistic when litellm has nothing to say about the model: a wrong `True` costs
    one call before the fallback takes over, whereas a wrong `False` would silently
    multiply input-token cost by k for the rest of the process.
    """
    key = (model_id, custom_llm_provider)
    if key in _N_SUPPORT:
        return _N_SUPPORT[key]

    if custom_llm_provider in _PROVIDERS_WITHOUT_N or model_id.split("/", 1)[0] in _PROVIDERS_WITHOUT_N:
        supported = False
    else:
        try:
            params = get_supported_openai_params(model=model_id, custom_llm_provider=custom_llm_provider)
        except Exception as e:
            # Unrecognised model name — litellm can't tell us, so let the request decide.
            logger.debug(f"Could not determine n>1 support for {model_id}: {e} — assuming supported")
            supported = True
        else:
            supported = params is None or "n" in params

    _N_SUPPORT[key] = supported
    return supported


def _completion_batched(kwargs: dict, samples: int, model_id: str, provider: str | None) -> list:
    """Try to get all `samples` choices from one n=samples call.

    Returns whatever choices came back — possibly none, possibly fewer than asked —
    and never raises. Records a negative result in _N_SUPPORT only when the error
    specifically identifies `n` as unsupported, never on a merely-failed request.
    """
    key = (model_id, provider)
    for attempt in range(_BATCHED_ATTEMPTS):
        try:
            choices = list(completion(**kwargs, n=samples).choices)
        except UnsupportedParamsError as e:
            # litellm's pre-flight refusal: the parameter is definitively unsupported for
            # this model, so record it and stop paying for the probe on later steps.
            _N_SUPPORT[key] = False
            logger.info(f"{model_id} does not support n>1 ({e}) — falling back to {samples} separate completions")
            return []
        except BadRequestError as e:
            # A 400 that is *not* specifically about an unsupported parameter. Both
            # ContextWindowExceededError and ContentPolicyViolationError subclass
            # BadRequestError, and neither says anything about `n` — so caching a negative
            # here would send every later step down the loop route, which re-bills the
            # trajectory prompt once per sample and caps at MAX_LOOP_SAMPLES, for the rest
            # of the process. Fall back for this step without recording a verdict.
            #
            # The cost of being conservative: a provider that rejects n>1 with a plain 400
            # instead of a typed UnsupportedParamsError pays one rejected call per step
            # rather than one per process. That is the cheaper mistake — a rejected request
            # is not billed for tokens — and the alternative, matching on message text,
            # would misclassify exactly the unrelated 400s this branch exists to protect.
            # Providers known to behave that way belong in _PROVIDERS_WITHOUT_N, which is
            # checked before any call is made.
            logger.debug(f"Batched resampling rejected for {model_id} ({e}) — falling back for this step only")
            return []
        except Exception as e:
            # Timeout, rate limit, 5xx: says nothing about n>1 support, so don't
            # poison the cache for the rest of the process.
            logger.debug(f"Batched resampling attempt {attempt + 1}/{_BATCHED_ATTEMPTS} failed for {model_id}: {e}")
            continue

        if len(choices) < samples:
            # Provider accepted `n` and ignored it (or litellm dropped it).
            _N_SUPPORT[key] = False
            logger.info(f"{model_id} returned {len(choices)} choices for n={samples} — topping up with separate completions")
        return choices

    return []


def _completion_single(kwargs: dict, model_id: str, index: int):
    """One completion, retried independently. Returns a choice, or None if it never landed."""
    # Every parallel call would otherwise share one messages list, and litellm rewrites
    # messages in place on some provider paths (system-message hoisting, cache_control
    # tagging). Copy the list and each message so k concurrent calls can't corrupt each
    # other's prompt. Values are shared, but nothing downstream mutates them.
    call_kwargs = {**kwargs, "messages": [dict(m) for m in kwargs["messages"]]}
    for attempt in range(_SINGLE_ATTEMPTS):
        try:
            choices = completion(**call_kwargs).choices
        except Exception as e:
            logger.debug(f"Sample {index} attempt {attempt + 1}/{_SINGLE_ATTEMPTS} failed for {model_id}: {e}")
            continue
        if choices:
            return choices[0]
        logger.debug(f"Sample {index} returned no choices for {model_id}")
    return None


def _completion_loop(kwargs: dict, count: int, model_id: str) -> list:
    """Get `count` samples as `count` separate single-completion calls.

    Bounded-parallel, since resampling a trajectory can mean max_samples × max_steps
    calls. Results are returned in submission order, not completion order, so debug
    artifacts are reproducible across runs.
    """
    workers = min(count, guidelines_settings.consistency_resample_max_workers)
    if workers <= 1:
        results = [_completion_single(kwargs, model_id, i) for i in range(count)]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="resample") as pool:
            results = list(pool.map(lambda i: _completion_single(kwargs, model_id, i), range(count)))

    choices = [c for c in results if c is not None]
    if len(choices) < count:
        logger.warning(f"{count - len(choices)} of {count} resampling calls to {model_id} failed after retries")
    return choices


def get_response_sampling(
    prompt,
    model_id: str,
    temperature: float,
    samples: int,
    max_token: int = MAX_NEW_TOKENS,
    stop=None,
    logprobs: bool = False,
    tools: list | None = None,
    custom_llm_provider: str | None = None,
) -> list:
    """Get `samples` sampled responses, whether or not the provider supports n>1.

    Tries one n=samples call when the model looks capable, then fills any shortfall
    with separate single-completion calls, capped at MAX_LOOP_SAMPLES because each of
    those re-bills the trajectory prompt. Returns a list of Choice objects compatible
    with extract_raw_samples() in resampling.py (handles both the .message.tool_calls
    and .message.content paths).

    Raises EvolveException only when fewer than two samples could be obtained at all
    (or none, for samples=1) — a partial result is scoreable, just noisier.
    """
    messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    # Dispatched once, OUTSIDE every attempt below, so retries and the k parallel
    # fallback calls all re-send the same redacted messages rather than re-leaking
    # (and re-tagging) the raw trajectory k times.
    llm_messages = dispatch_llm_pre_call(messages, purpose="consistency_resampling", model=model_id)
    kwargs: dict = dict(
        model=model_id,
        messages=llm_messages,
        temperature=temperature,
        max_tokens=max_token,
    )
    if custom_llm_provider:
        kwargs["custom_llm_provider"] = custom_llm_provider
    if stop:
        kwargs["stop"] = stop
    if logprobs:
        kwargs["logprobs"] = logprobs
    if tools:
        kwargs["tools"] = tools

    choices: list = []
    if samples > 1 and _supports_n(model_id, custom_llm_provider):
        choices = _completion_batched(kwargs, samples, model_id, custom_llm_provider)

    target = samples
    if len(choices) < samples:
        # The loop route bills the whole trajectory prompt once per sample, so the
        # configured count is capped here regardless of what the config asked for.
        # Choices the batched attempt already returned are kept even when they exceed
        # the cap — they cost one prompt between them and are already paid for.
        target = min(samples, MAX_LOOP_SAMPLES)
        if target < samples:
            _log_loop_cap(model_id, custom_llm_provider, samples, target)
        if len(choices) < target:
            # No `seed` here on purpose: pinning one would collapse the very diversity
            # the consistency metric is measuring.
            choices += _completion_loop(kwargs, target - len(choices), model_id)

    required = min(MIN_USABLE_SAMPLES, target)
    if len(choices) < required:
        raise EvolveException(
            f"Requested {target} samples from {model_id} but only obtained {len(choices)}. "
            f"Consistency scoring requires at least {required}."
        )
    if len(choices) < target:
        logger.warning(
            f"Requested {target} samples from {model_id} but obtained {len(choices)}. "
            "Consistency scores will be based on fewer samples than configured."
        )
    return choices
