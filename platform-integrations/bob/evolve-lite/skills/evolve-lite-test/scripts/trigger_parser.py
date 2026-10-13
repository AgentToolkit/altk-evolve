#!/usr/bin/env python3
"""
trigger_parser.py — Convert a skill trigger phrase into a natural user question.

Public API
----------
    trigger_to_user_question(trigger, llm_fn=None) -> str

If ``llm_fn`` is supplied it is called with the trigger string and must return
a rephrased natural-language question.  This is the LLM path.

If ``llm_fn`` is None (default) the function falls back to a structural
decomposition of the trigger phrase:
  1. Strip leading conditional word ("When", "After", "While", "Before", "If").
  2. Decompose the remaining text into (action, subject, condition) chunks.
  3. Select one of several first-person question templates based on the
     detected *scenario type* (error recovery, setup, procedural, creation,
     generic).

LLM helper
----------
To use an OpenAI-compatible backend, pass a closure as ``llm_fn``:

    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def llm_fn(trigger):
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You convert skill trigger phrases into natural user questions. "
                    "The trigger describes WHEN a skill applies. "
                    "Return a single realistic first-person question a developer "
                    "would ask that would naturally invoke this skill. "
                    "Do not mention the skill or use meta-language. "
                    "Return only the question, no extra text."
                )},
                {"role": "user", "content": trigger},
            ],
            temperature=0.3,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()

    question = trigger_to_user_question(trigger, llm_fn=llm_fn)
"""

import re

# ---------------------------------------------------------------------------
# Scenario-type detection → question templates
# ---------------------------------------------------------------------------

# Each entry: (regex against raw trigger, question_fn(trigger) -> str)
# Checked in order; first match wins.
# question_fn receives the *original* trigger so it can extract key nouns
# directly rather than using the grammatically awkward stripped core.
_SCENARIO_RULES = [
    # Token expiration / reauth
    (
        r"token expir",
        lambda t: (
            "I ran a Watson Orchestrate CLI command and it failed with a token "
            "expiration error. How do I reauthenticate?"
        ),
    ),
    # Interactive prompt / automation / piping
    (
        r"interact.*prompt|prompt.*interact|automat.*prompt|stdin|piping",
        lambda t: (
            "I'm trying to automate a Watson Orchestrate CLI command but it "
            "keeps stopping to ask for a password interactively. "
            "How do I run it non-interactively?"
        ),
    ),
    # Auth / credential error mid-session
    (
        r"auth.*error|error.*auth|needs to be authenticated|authenticated before",
        lambda t: (
            "I'm about to run Watson Orchestrate CLI commands. "
            "What do I need to do to make sure I'm authenticated first?"
        ),
    ),
    # First-time setup / registering env
    (
        r"first time|setting up|set up|register.*env|env.*register",
        lambda t: (
            "I'm setting up the Watson Orchestrate CLI environment for the "
            "first time. What are the steps I need to follow?"
        ),
    ),
    # Import YAML agent
    (
        r"import.*agent|agents import",
        lambda t: (
            "I've got my agent YAML ready. "
            "How do I import it into Watson Orchestrate?"
        ),
    ),
    # Deploy / activate after import
    (
        r"deploy|not yet active|not.*active|needs to be made active|activate.*agent",
        lambda t: (
            "I just imported my Watson Orchestrate agent but it doesn't seem "
            "to be active. What's the next step to make it available?"
        ),
    ),
    # Create YAML / spec_version
    (
        r"yaml.*file|spec_version|yaml.*definition|create.*agent.*yaml",
        lambda t: (
            "I need to create the YAML definition file for a Watson Orchestrate "
            "agent. What fields are required — things like spec_version, name, "
            "and instructions?"
        ),
    ),
    # Activate existing venv / CLI not found — must come BEFORE generic venv rule
    (
        r"CLI.*not found|not found.*CLI|CLI command is not found|activate.*before running",
        lambda t: (
            "I opened a new terminal and now the orchestrate CLI command isn't "
            "found. How do I fix this?"
        ),
    ),
    # Virtual environment / venv creation
    (
        r"virtual environment|venv|virtualenv|\.venv|isolated environment|dependency conflict",
        lambda t: (
            "I need to set up an isolated Python environment for my project. "
            "What's the correct way to create and activate a virtual environment?"
        ),
    ),
    # Requirements / dependencies
    (
        r"requirements.*file|requirements\.txt|reproducible.*list|package.*depend",
        lambda t: (
            "I need to create a requirements.txt for my Python project so "
            "dependencies are reproducible. How should I do that?"
        ),
    ),
    # Environment variables / .env / secrets
    (
        r"environment.*secret|\.env|env.*variable|secrets.*runtime|config.*runtime",
        lambda t: (
            "My application needs to read secrets and config values at runtime. "
            "What's the right way to manage environment variables?"
        ),
    ),
    # Multi-tool / multiple functions — must come BEFORE single-tool register rule
    (
        r"multiple.*function|multi.*tool|expose.*multiple",
        lambda t: (
            "I have a Python file with several functions I want to expose as "
            "Watson Orchestrate tools. How do I import them all?"
        ),
    ),
    # Tool not recognized / import error
    (
        r"not.*recognized|not being recognized|tools import command",
        lambda t: (
            "I imported my Python tool into Watson Orchestrate but it's not "
            "being recognized. What could be wrong?"
        ),
    ),
    # Skill-flow / quality gate / missing references
    (
        r"quality gate|missing.*skill|skill.*missing|atomic skill.*ref",
        lambda t: (
            "The skill-flow quality gate is reporting missing atomic skill "
            "references, but those skills exist in my library. How do I fix this?"
        ),
    ),
    # Generic error / failure / not found
    (
        r"\berror\b|\bfail\b|\bnot found\b|\bcannot\b|\binvalid\b|\bwrong\b|\bmissing\b",
        lambda t: (
            f"I'm running into an issue: {_extract_error_context(t)}. "
            "What's the correct way to fix this?"
        ),
    ),
]

_GENERIC_TEMPLATE = lambda trigger: (
    f"I'm trying to {_trigger_as_task(trigger)} and ran into a problem. "
    "What should I do?"
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _strip_conditional(trigger: str) -> str:
    """Remove leading 'When', 'After', 'While', 'Before', 'If' clause."""
    return re.sub(
        r"^(When|After|While|Before|If)\s+",
        "",
        trigger.strip(),
        flags=re.IGNORECASE,
    ).strip()


def _lower_first(text: str) -> str:
    return text[0].lower() + text[1:] if text else text


def _shorten(text: str, max_words: int = 10) -> str:
    """Trim to the first ``max_words`` words to keep templates readable."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",.;:") + "…"


def _extract_subject(text: str) -> str:
    """Pull the first noun phrase (up to 4 words) out of the core text."""
    words = text.split()
    return " ".join(words[:4]).rstrip(",.;:") if words else text


def _trigger_as_task(trigger: str) -> str:
    """Convert trigger to a short task phrase for the generic template.

    Strips the conditional prefix and converts to lower-case verb phrase,
    capping at 10 words.
    """
    core = _lower_first(_strip_conditional(trigger))
    # If the core looks like a noun clause ("a Python project needs…"),
    # try to find the first verb and trim before it for readability.
    # Simple heuristic: cut at the first occurrence of " needs ", " is ", " has ".
    for pivot in (" needs ", " is ", " has ", " was ", " requires "):
        idx = core.find(pivot)
        if idx > 0:
            core = core[:idx].strip()
            break
    return _shorten(core)


def _extract_error_context(trigger: str) -> str:
    """Pull a concise error description from the trigger for error templates."""
    core = _strip_conditional(trigger)
    # Grab up to 8 words following "error", "fail", "not found", etc.
    match = re.search(
        r"(error|fail(?:ure)?|not found|cannot|invalid|wrong|missing)[^,;.]*",
        core, re.IGNORECASE,
    )
    if match:
        return _shorten(match.group(0).strip(), max_words=8)
    return _shorten(core, max_words=8)


# ---------------------------------------------------------------------------
# Structural decomposition (offline fallback)
# ---------------------------------------------------------------------------

def _structural_question(trigger: str) -> str:
    """Derive a question from ``trigger`` purely via regex + templates."""
    for pattern, question_fn in _SCENARIO_RULES:
        if re.search(pattern, trigger, re.IGNORECASE):
            return question_fn(trigger)

    return _GENERIC_TEMPLATE(trigger)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trigger_to_user_question(trigger: str, llm_fn=None) -> str:
    """Convert a skill trigger phrase into a natural user question.

    Parameters
    ----------
    trigger:
        The ``trigger:`` frontmatter value from an entity file, e.g.
        "When Watson Orchestrate CLI reports a token expiration error".
    llm_fn:
        Optional callable ``(trigger: str) -> str``.  If provided it is called
        with the raw trigger and must return a rephrased question string.
        If None, structural decomposition is used instead.

    Returns
    -------
    str
        A natural first-person question that would plausibly invoke this skill.
    """
    if llm_fn is not None:
        try:
            result = llm_fn(trigger)
            if result and isinstance(result, str):
                return result.strip()
        except Exception as exc:  # noqa: BLE001
            import sys
            print(f"  Warning: llm_fn failed ({exc}), falling back to structural parse",
                  file=sys.stderr)

    return _structural_question(trigger)
