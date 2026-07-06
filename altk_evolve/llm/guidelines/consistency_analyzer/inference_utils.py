"""
LLM inference utilities for the consistency analyzer.

Replaces the original IBM-specific provider dispatch (RITS, WatsonX, IBM LiteLLM)
with a single litellm.completion() call, matching the pattern used throughout the
rest of altk-evolve (see altk_evolve/llm/guidelines/guidelines.py).

The three provider-named functions (get_response_rits_sampling,
get_response_ibm_litellm_sampling, get_response_watsonx_sampling) are kept as
thin aliases so resampling.py does not need to change.
"""

from litellm import completion

from altk_evolve.config.llm import llm_settings

MAX_NEW_TOKENS = 3000



def get_response_sampling(
    prompt,
    model_id: str,
    temperature: float,
    samples: int,
    max_token: int = MAX_NEW_TOKENS,
    stop=None,
    logprobs: bool = False,
    tools: list = [],
) -> list:
    """Get multiple sampled responses via litellm.completion().

    Returns a list of Choice objects compatible with extract_raw_samples() in
    resampling.py (handles both the .message.tool_calls and .message.content paths).
    """
    messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    kwargs: dict = dict(
        model=model_id,
        messages=messages,
        temperature=temperature,
        max_tokens=max_token,
        n=samples,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    if stop:
        kwargs["stop"] = stop
    if logprobs:
        kwargs["logprobs"] = logprobs
    if tools:
        kwargs["tools"] = tools

    response = completion(**kwargs)
    return response.choices

