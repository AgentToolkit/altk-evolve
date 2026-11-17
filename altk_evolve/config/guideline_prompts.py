"""Which prompt the standard guideline generator renders.

``guidelines_prompt`` (``EVOLVE_GUIDELINES_PROMPT``, or the guideline processor's config in a
processing profile) is always a prompt *name*, never a path. A name refers to one of:

- a prompt shipped with Evolve: ``<name>.jinja2`` in ``altk_evolve/llm/guidelines/prompts/generation``.
  Shipping another out-of-the-box prompt is adding a file there.
- a prompt of the deployment's own: ``<name>.jinja2`` in ``EVOLVE_GUIDELINES_PROMPTS_DIR``, when set.

Profiles can be written remotely (MCP ``ensure_processing_profile``, REST), so they can only
choose among files that Evolve or the server's operator put in place, and never read an
arbitrary file into a prompt. A deployment's prompt may not reuse a shipped name, so a name
like ``default`` means the same prompt everywhere.

A template may use only the variables in ``TEMPLATE_VARIABLES``, the ones the generator passes;
anything else is rejected when the prompt is resolved, rather than rendering as an empty string.

Resolving a name touches only the filesystem, so the config models can validate it without
importing the generator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, meta

DEFAULT_GUIDELINES_PROMPT = "default"

SHIPPED_PROMPTS_DIR = Path(__file__).parents[1] / "llm" / "guidelines" / "prompts" / "generation"

# The variables the standard generator renders a prompt with. The last five are set only when
# the trajectory has an outcome; test for one with ``{% if outcome %}``.
TEMPLATE_VARIABLES = frozenset(
    {
        "task_instruction",
        "trajectory_summary",
        "num_steps",
        "supporting_context",
        "rules_section",
        "constrained_decoding_supported",
        "outcome",
        "failed_checks",
        "omitted_checks",
        "detail",
        "segmented",
    }
)

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_DESCRIPTION = re.compile(r"\s*\{#-?(.*?)-?#\}", re.DOTALL)


@dataclass(frozen=True)
class GuidelinesPrompt:
    name: str
    path: Path
    source: str  # "shipped" or "user"

    @property
    def description(self) -> str:
        """A one-line summary for listings.

        The text of a leading Jinja comment, ``{#- ... -#}`` (the dashes keep it from adding a
        newline to the rendered prompt), or else the template's first non-empty line.
        """
        text = self.path.read_text()
        if match := _DESCRIPTION.match(text):
            return " ".join(match.group(1).split())
        return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _prompts_in(directory: Path, source: str) -> dict[str, GuidelinesPrompt]:
    return {
        path.stem: GuidelinesPrompt(path.stem, path, source) for path in sorted(directory.glob("*.jinja2")) if _NAME.fullmatch(path.stem)
    }


def _user_prompts_dir() -> Path | None:
    from altk_evolve.config.evolve import evolve_config

    return evolve_config.guidelines_prompts_dir


def available_guidelines_prompts() -> dict[str, GuidelinesPrompt]:
    """Every selectable prompt by name: those shipped with Evolve, then the deployment's own.

    Raises ``ValueError`` if ``EVOLVE_GUIDELINES_PROMPTS_DIR`` is set but is not a directory, or
    if one of its prompts reuses a shipped name.
    """
    prompts = _prompts_in(SHIPPED_PROMPTS_DIR, "shipped")
    user_dir = _user_prompts_dir()
    if user_dir is None:
        return prompts
    user_dir = Path(user_dir).expanduser()
    if not user_dir.is_dir():
        raise ValueError(f"EVOLVE_GUIDELINES_PROMPTS_DIR is not a directory: {user_dir}")
    user = _prompts_in(user_dir, "user")
    if clash := sorted(prompts.keys() & user.keys()):
        raise ValueError(
            f"Guideline prompts in {user_dir} reuse names shipped with Evolve: {', '.join(clash)}. "
            "Rename them, so that a shipped name always means the same prompt."
        )
    return prompts | user


def resolve_guidelines_prompt(name: str) -> Path:
    """Return the template file for a prompt name.

    Raises ``ValueError`` for a malformed or unknown name, or a template that uses a variable the
    generator does not pass, so a bad setting fails when a profile is published or the runtime is
    built, not at the first trajectory.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
        raise ValueError(f"guidelines_prompt must be a prompt name (letters, digits, '-' and '_'), not {name!r}")
    prompts = available_guidelines_prompts()
    prompt = prompts.get(name.strip())
    if prompt is None:
        raise ValueError(f"Unknown guidelines_prompt {name.strip()!r}: available prompts are {', '.join(prompts)}")
    check_template_variables(prompt.path)
    return prompt.path


def check_template_variables(path: Path) -> None:
    """Raise ``ValueError`` if the template uses a variable the generator does not pass."""
    unknown = _undeclared_variables(path) - TEMPLATE_VARIABLES
    if unknown:
        raise ValueError(
            f"Guideline prompt {path} uses variables the generator does not pass: {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(TEMPLATE_VARIABLES))}"
        )


@lru_cache(maxsize=64)
def _undeclared_variables(path: Path) -> frozenset[str]:
    # Read once per process, like the template itself: an edited prompt takes effect on restart.
    return frozenset(meta.find_undeclared_variables(Environment().parse(path.read_text())))
