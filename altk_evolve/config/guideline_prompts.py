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

from jinja2 import Environment, Template, TemplateSyntaxError, meta

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
        text = self.path.read_text(encoding="utf-8")
        if match := _DESCRIPTION.match(text):
            return " ".join(match.group(1).split())
        return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _prompts_in(directory: Path, source: str) -> dict[str, GuidelinesPrompt]:
    return {
        path.stem: GuidelinesPrompt(path.stem, path, source)
        for path in sorted(directory.glob("*.jinja2"))
        if path.is_file() and _NAME.fullmatch(path.stem)
    }


def _user_prompts_dir() -> Path | None:
    from altk_evolve.config.evolve import evolve_config

    return evolve_config.guidelines_prompts_dir


def _available(user_dir: Path | None, shipped_dir: Path) -> dict[str, GuidelinesPrompt]:
    prompts = _prompts_in(shipped_dir, "shipped")
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


def available_guidelines_prompts() -> dict[str, GuidelinesPrompt]:
    """Every selectable prompt by name, as the directories are now: those shipped with Evolve,
    then the deployment's own.

    Raises ``ValueError`` if ``EVOLVE_GUIDELINES_PROMPTS_DIR`` is set but is not a directory, or
    if one of its prompts reuses a shipped name.
    """
    return _available(_user_prompts_dir(), SHIPPED_PROMPTS_DIR)


def resolve_guidelines_prompt(name: str) -> Path:
    """Return the template file for a prompt name, once the template has loaded and checked out.

    Raises ``ValueError`` for a malformed or unknown name, or a template that can't be used (see
    ``load_guidelines_template``), so a bad setting fails when a profile is published, not at the
    first trajectory.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
        raise ValueError(f"guidelines_prompt must be a prompt name (letters, digits, '-' and '_'), not {name!r}")
    path = _lookup(name.strip(), _user_prompts_dir(), SHIPPED_PROMPTS_DIR)
    _compiled(path)
    return path


def load_guidelines_template(name: str) -> Template:
    """The compiled template for a prompt name.

    A name found once is not looked up again in this process, so a file added later (one that
    clashes with a shipped name, say) can't break a prompt already in use. A template is read
    once, and checked and compiled from that same text, so an edit takes effect on restart and
    can't skip the checks.
    """
    return _compiled(resolve_guidelines_prompt(name))


@lru_cache(maxsize=64)
def _lookup(name: str, user_dir: Path | None, shipped_dir: Path) -> Path:
    # Only a successful lookup is cached; an unknown name is looked up again next time.
    prompts = _available(user_dir, shipped_dir)
    prompt = prompts.get(name)
    if prompt is None:
        raise ValueError(f"Unknown guidelines_prompt {name!r}: available prompts are {', '.join(prompts)}")
    return prompt.path


@lru_cache(maxsize=64)
def _compiled(path: Path) -> Template:
    """Read, check and compile one template. Every failure is a ``ValueError``, which pydantic and
    the MCP handlers report as an invalid setting."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"Guideline prompt {path} can't be read: {e}") from e
    try:
        tree = Environment().parse(source)
    except TemplateSyntaxError as e:
        raise ValueError(f"Guideline prompt {path} is not a valid Jinja2 template (line {e.lineno}): {e.message}") from e
    referenced = list(meta.find_referenced_templates(tree))
    if referenced:
        names = ", ".join(sorted("a computed name" if ref is None else repr(ref) for ref in referenced))
        raise ValueError(
            f"Guideline prompt {path} includes, imports or extends other templates ({names}). "
            "A guideline prompt must be a single, self-contained template."
        )
    unknown = meta.find_undeclared_variables(tree) - TEMPLATE_VARIABLES
    if unknown:
        raise ValueError(
            f"Guideline prompt {path} uses variables the generator does not pass: {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(sorted(TEMPLATE_VARIABLES))}"
        )
    return Template(source)
