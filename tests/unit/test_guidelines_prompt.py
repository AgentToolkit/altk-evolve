"""guidelines_prompt names the prompt the standard guideline generator renders."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import altk_evolve.config.guideline_prompts as guideline_prompts
from altk_evolve.cli.cli import app
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.guideline_prompts import SHIPPED_PROMPTS_DIR, available_guidelines_prompts, resolve_guidelines_prompt
from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.processing import ProcessingError, ProcessingManager, ProcessorRegistry
from altk_evolve.processing.builtin import GuidelineConfig, GuidelineProcessor

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("default_trajectory_window")]

runner = CliRunner()


def _config():
    # Looked up at call time: other tests reload altk_evolve.config.evolve, replacing its config object.
    import altk_evolve.config.evolve

    return altk_evolve.config.evolve.evolve_config


@pytest.fixture
def prompts_dir(tmp_path, monkeypatch):
    """A deployment's own prompts directory, set as EVOLVE_GUIDELINES_PROMPTS_DIR would."""
    directory = tmp_path / "prompts"
    directory.mkdir()
    (directory / "mine.jinja2").write_text("{#- My own prompt. -#}\nMine, for {{task_instruction}}")
    monkeypatch.setattr(_config(), "guidelines_prompts_dir", directory)
    return directory


def _manager() -> ProcessingManager:
    registry = ProcessorRegistry()
    registry.register(GuidelineProcessor)
    return ProcessingManager(registry=registry)


def _profile(**config):
    return {"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": config}]}


def _render(monkeypatch, options: GuidelineRuntime, **kwargs) -> str:
    """Run one generation call and return the prompt the model was sent."""
    import altk_evolve.llm.guidelines.guidelines as guidelines

    sent: list[str] = []

    def completion(*, messages, **kwargs):
        sent.append(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"guidelines": []}'))])

    monkeypatch.setattr(guidelines, "completion", completion)
    monkeypatch.setattr(guidelines, "dispatch_llm_pre_call", lambda messages, **kwargs: messages)
    guidelines._generate_guidelines_for_segment("Add a field", "1. edit a file", 1, True, options=options, **kwargs)
    return sent[0]


# --- Defaults ---------------------------------------------------------------------------------


def test_the_default_prompt_is_the_shipped_default(monkeypatch):
    monkeypatch.delenv("EVOLVE_GUIDELINES_PROMPT", raising=False)
    monkeypatch.delenv("EVOLVE_GUIDELINES_PROMPTS_DIR", raising=False)

    config = EvolveConfig(_env_file=None)
    assert (config.guidelines_prompt, config.guidelines_prompts_dir) == ("default", None)
    assert GuidelineRuntime().guidelines_prompt == "default"
    assert resolve_guidelines_prompt("default") == SHIPPED_PROMPTS_DIR / "default.jinja2"


def test_the_default_prompt_renders_the_template_unchanged(monkeypatch):
    # Its description comment renders as nothing, so the prompt starts as it always has.
    prompt = _render(monkeypatch, GuidelineRuntime())

    assert prompt.startswith("Extract actionable, relevant guidelines from this trajectory")
    assert "Add a field" in prompt


def test_settings_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EVOLVE_GUIDELINES_PROMPT", "mine")
    monkeypatch.setenv("EVOLVE_GUIDELINES_PROMPTS_DIR", str(tmp_path))

    config = EvolveConfig(_env_file=None)
    assert (config.guidelines_prompt, config.guidelines_prompts_dir) == ("mine", tmp_path)


def test_an_empty_prompts_dir_means_unset(monkeypatch):
    monkeypatch.setenv("EVOLVE_GUIDELINES_PROMPTS_DIR", "")

    assert EvolveConfig(_env_file=None).guidelines_prompts_dir is None


# --- Choosing a prompt ------------------------------------------------------------------------


def test_a_prompt_in_the_prompts_dir_is_rendered_by_name(monkeypatch, prompts_dir):
    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt="mine")) == "Mine, for Add a field"


def test_shipping_a_prompt_is_adding_a_file(monkeypatch, tmp_path):
    shipped = tmp_path / "generation"
    shipped.mkdir()
    (shipped / "default.jinja2").write_text("Default for {{task_instruction}}")
    (shipped / "terse.jinja2").write_text("Terse for {{task_instruction}}")
    monkeypatch.setattr(guideline_prompts, "SHIPPED_PROMPTS_DIR", shipped)

    assert {name: p.source for name, p in available_guidelines_prompts().items()} == {"default": "shipped", "terse": "shipped"}
    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt="terse")) == "Terse for Add a field"


def test_listing_puts_shipped_prompts_first_with_descriptions(prompts_dir):
    prompts = available_guidelines_prompts()

    assert [(p.name, p.source) for p in prompts.values()] == [
        ("default", "shipped"),
        ("environment-grounded", "shipped"),
        ("mine", "user"),
    ]
    assert prompts["mine"].description == "My own prompt."
    assert prompts["default"].description.startswith("Evolve's general-purpose prompt")


# --- Rejected settings ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "typo",
        "",
        " ",
        "../default",
        "/etc/passwd",
        "prompts/mine",
        "mine.jinja2",
        " default",
        "default\n",
        "default ",
        "a.b",
        "-mine",
        "_mine",
    ],
)
def test_anything_but_an_available_name_is_rejected(prompts_dir, name):
    with pytest.raises(ValueError, match="guidelines_prompt"):
        GuidelineConfig(guidelines_prompt=name)


def test_a_name_is_used_exactly_as_given(monkeypatch, prompts_dir):
    # Letters, digits, '-' and '_' are allowed anywhere after the first character.
    (prompts_dir / "My_prompt-2.jinja2").write_text("Mixed, for {{task_instruction}}")

    assert GuidelineConfig(guidelines_prompt="My_prompt-2").guidelines_prompt == "My_prompt-2"
    # Padding is rejected, not stripped: the manifest would otherwise record " default" as a new prompt.
    with pytest.raises(ValueError, match="no surrounding whitespace"):
        _manager().validate(_profile(guidelines_prompt=" default"))


def test_a_runtime_does_not_check_the_prompt_until_it_renders(monkeypatch):
    # The consistency pipelines and from_settings() build runtimes too; they must not fail on a
    # prompt only the standard generator uses. The standard generator rejects it when it renders.
    runtime = GuidelineRuntime(guidelines_prompt="typo")
    with pytest.raises(ValueError, match="Unknown guidelines_prompt 'typo'"):
        _render(monkeypatch, runtime)


def test_a_bad_environment_prompt_does_not_break_a_profile_that_sets_its_own(monkeypatch):
    monkeypatch.setattr(_config(), "guidelines_prompt", "bogus")

    assert GuidelineConfig(guidelines_prompt="environment-grounded").guidelines_prompt == "environment-grounded"
    assert GuidelineRuntime.from_settings().guidelines_prompt == "bogus"  # no longer raises
    # A consistency-only profile records the prompt but never renders it.
    assert GuidelineConfig(guidelines_mode="consistency").guidelines_prompt == "bogus"


def test_a_bad_environment_prompt_is_named_when_a_profile_uses_it(monkeypatch):
    monkeypatch.setattr(_config(), "guidelines_prompt", "bogus")

    with pytest.raises(ValueError, match="EVOLVE_GUIDELINES_PROMPT='bogus' is not a usable prompt"):
        GuidelineConfig()
    with pytest.raises(ProcessingError, match="EVOLVE_GUIDELINES_PROMPT"):
        _manager().validate(_profile())


def test_a_user_prompt_may_not_reuse_a_shipped_name(prompts_dir):
    (prompts_dir / "default.jinja2").write_text("Not the default")

    with pytest.raises(ValueError, match="reuse names shipped with Evolve: default"):
        resolve_guidelines_prompt("default")


def test_a_prompts_dir_that_does_not_exist_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(_config(), "guidelines_prompts_dir", tmp_path / "missing")

    with pytest.raises(ValueError, match="not a directory"):
        resolve_guidelines_prompt("default")


def test_a_template_using_a_variable_the_generator_does_not_pass_is_rejected(prompts_dir):
    (prompts_dir / "typo.jinja2").write_text("For {{task_instructions}}")

    with pytest.raises(ValueError, match="task_instructions"):
        resolve_guidelines_prompt("typo")


@pytest.mark.parametrize(
    "source",
    [
        "{% include 'x.txt' %}{{ task_instruction }}",
        "{% import 'macros.jinja2' as m %}{{ task_instruction }}",
        "{% extends 'default.jinja2' %}",
        "{% from 'm.jinja2' import x %}{{ task_instruction }}",
    ],
    ids=["include", "import", "extends", "from-import"],
)
def test_a_template_that_references_other_templates_is_rejected(prompts_dir, source):
    # They would pass the variable check and then fail at the first render: there is no loader.
    (prompts_dir / "refs.jinja2").write_text(source)

    with pytest.raises(ValueError, match="includes, imports or extends other templates"):
        GuidelineConfig(guidelines_prompt="refs")


def test_a_template_with_a_syntax_error_is_an_invalid_setting(prompts_dir):
    (prompts_dir / "broken.jinja2").write_text("{% if %}x")

    with pytest.raises(ValueError, match="not a valid Jinja2 template"):
        GuidelineConfig(guidelines_prompt="broken")
    with pytest.raises(ProcessingError):
        _manager().put("broken", _profile(guidelines_prompt="broken"), expected_revision=0)


def test_a_directory_named_like_a_prompt_is_not_a_prompt(prompts_dir):
    (prompts_dir / "folder.jinja2").mkdir()

    assert "folder" not in available_guidelines_prompts()
    with pytest.raises(ValueError, match="Unknown guidelines_prompt 'folder'"):
        GuidelineConfig(guidelines_prompt="folder")


def test_a_template_is_checked_and_compiled_from_one_read(monkeypatch, prompts_dir):
    # An edit after the prompt is first used can neither take effect nor skip the variable check.
    path = prompts_dir / "once.jinja2"
    path.write_text("Once, for {{task_instruction}}")
    runtime = GuidelineRuntime(guidelines_prompt="once")
    assert _render(monkeypatch, runtime) == "Once, for Add a field"

    path.write_text("Edited, for {{ notpassed }}")
    assert _render(monkeypatch, runtime) == "Once, for Add a field"


def test_a_prompt_in_use_survives_a_clashing_file_added_later(monkeypatch, prompts_dir):
    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt="mine")) == "Mine, for Add a field"
    (prompts_dir / "default.jinja2").write_text("A clash")

    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt="mine")) == "Mine, for Add a field"
    # A name not yet looked up is: the clash is reported then.
    with pytest.raises(ValueError, match="reuse names shipped with Evolve"):
        resolve_guidelines_prompt("environment-grounded")


def test_prompts_are_read_as_utf8(monkeypatch, prompts_dir):
    (prompts_dir / "accents.jinja2").write_bytes("{#- Café prompt. -#}\nCafé, for {{task_instruction}}".encode())

    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt="accents")) == "Café, for Add a field"
    assert available_guidelines_prompts()["accents"].description == "Café prompt."


def test_the_generator_passes_every_documented_variable(monkeypatch, prompts_dir):
    # A template that prints each variable renders without a gap, with and without an outcome.
    from altk_evolve.config.guideline_prompts import TEMPLATE_VARIABLES
    from altk_evolve.processing.models import TrajectoryOutcome
    import altk_evolve.llm.guidelines.guidelines as guidelines

    (prompts_dir / "all.jinja2").write_text("\n".join(f"{v}={{{{ {v} is defined }}}}" for v in sorted(TEMPLATE_VARIABLES)))
    sent: list[str] = []
    monkeypatch.setattr(
        guidelines,
        "completion",
        lambda *, messages, **kw: (
            sent.append(messages[0]["content"])
            or SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"guidelines": []}'))])
        ),
    )
    monkeypatch.setattr(guidelines, "dispatch_llm_pre_call", lambda messages, **kwargs: messages)
    options = GuidelineRuntime(guidelines_prompt="all")
    guidelines._generate_guidelines_for_segment("t", "s", 1, True, options=options, outcome=TrajectoryOutcome(success=False))
    guidelines._generate_guidelines_for_segment("t", "s", 1, True, options=options)

    with_outcome, without = sent
    assert "=False" not in with_outcome
    undefined_without = {line.split("=")[0] for line in without.splitlines() if line.endswith("=False")}
    assert undefined_without == {"outcome", "failed_checks", "omitted_checks", "detail", "segmented"}


# --- Processing profiles ----------------------------------------------------------------------


def test_a_profile_can_choose_its_prompt_and_records_it(prompts_dir):
    plan = _manager().validate(_profile(guidelines_prompt="mine"))

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "mine"


def test_a_profile_without_a_prompt_captures_the_environment_default(monkeypatch, prompts_dir):
    monkeypatch.setattr(_config(), "guidelines_prompt", "mine")
    plan = _manager().validate(_profile())

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "mine"


def test_a_profile_stored_before_the_setting_existed_replays_with_the_default_prompt(monkeypatch, prompts_dir):
    manager = _manager()
    legacy = manager.validate(_profile()).manifest()
    del legacy["processors"][0]["config"]["guidelines_prompt"]
    manager.repository.put("legacy", legacy, expected_revision=0)

    monkeypatch.setattr(_config(), "guidelines_prompt", "mine")
    plan = manager.resolve("legacy", revision=1)

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "default"


@pytest.mark.parametrize("name", ["typo", "/etc/passwd"])
def test_a_profile_naming_an_unavailable_prompt_is_not_published(name):
    with pytest.raises(ProcessingError):
        _manager().put("bad-prompt", _profile(guidelines_prompt=name), expected_revision=0)


def test_a_profile_cannot_set_the_prompts_dir(tmp_path):
    with pytest.raises(ProcessingError):
        _manager().put("bad-dir", _profile(guidelines_prompts_dir=str(tmp_path)), expected_revision=0)


# --- CLI --------------------------------------------------------------------------------------


def test_cli_lists_prompts_and_marks_the_selected_one(monkeypatch, prompts_dir):
    monkeypatch.setattr(_config(), "guidelines_prompt", "mine")

    result = runner.invoke(app, ["prompts", "list"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "default" in result.output and "shipped" in result.output
    assert "mine *" in result.output and "My own prompt." in result.output


@pytest.mark.parametrize("selected", ["bogus", "badvars"])
def test_cli_list_reports_a_selection_that_cant_be_used(monkeypatch, prompts_dir, selected):
    # An unknown name, or a listed prompt the generator would reject: say so, mark nothing, exit 1.
    (prompts_dir / "badvars.jinja2").write_text("For {{ notpassed }}")
    monkeypatch.setattr(_config(), "guidelines_prompt", selected)

    result = runner.invoke(app, ["prompts", "list"], env={"COLUMNS": "200"})

    assert result.exit_code == 1
    assert f"EVOLVE_GUIDELINES_PROMPT='{selected}' can't be used" in result.output
    assert " *" not in result.output


def test_cli_shows_a_template(prompts_dir):
    result = runner.invoke(app, ["prompts", "show", "default"])

    assert result.exit_code == 0, result.output
    assert result.output == (SHIPPED_PROMPTS_DIR / "default.jinja2").read_text()


def test_cli_reports_a_bad_prompts_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(_config(), "guidelines_prompts_dir", tmp_path / "missing")

    result = runner.invoke(app, ["prompts", "list"])

    assert result.exit_code == 1


# --- The environment-grounded prompt ----------------------------------------------------------

REQUIREMENTS_HEADING = "# Requirements the environment stated"
GENERIC_RULE_5 = "values specific to this task."
NAMED_RULE_5 = (
    "values specific to this one task. Keep the names of the project's own files, commands, formats and "
    "conventions when a guideline depends on them."
)


def _template(name: str) -> str:
    """A shipped template without its description comment, the one line that should differ."""
    text = (SHIPPED_PROMPTS_DIR / f"{name}.jinja2").read_text()
    assert text.startswith("{#-")
    return text.split("\n", 1)[1]


def test_environment_grounded_is_the_default_plus_two_edits():
    # A change to default.jinja2 must be made here too: this fails until it is.
    variant = _template("environment-grounded")
    start = variant.index(REQUIREMENTS_HEADING)
    end = variant.index("# Quality threshold", start)

    without_edits = (variant[:start] + variant[end:]).replace(NAMED_RULE_5, GENERIC_RULE_5)

    assert without_edits == _template("default")
    assert variant.count(NAMED_RULE_5) == 1


def test_environment_grounded_is_selectable_and_uses_only_known_variables():
    assert resolve_guidelines_prompt("environment-grounded") == SHIPPED_PROMPTS_DIR / "environment-grounded.jinja2"


@pytest.mark.parametrize("with_outcome", [False, True])
def test_environment_grounded_asks_for_stated_requirements_with_and_without_an_outcome(monkeypatch, with_outcome):
    from altk_evolve.processing.models import TrajectoryOutcome

    kwargs = {"outcome": TrajectoryOutcome(success=False)} if with_outcome else {}
    default = _render(monkeypatch, GuidelineRuntime(), **kwargs)
    variant = _render(monkeypatch, GuidelineRuntime(guidelines_prompt="environment-grounded"), **kwargs)

    assert REQUIREMENTS_HEADING not in default
    assert REQUIREMENTS_HEADING in variant
    assert ("Keep the names of the project's own files" in variant) == with_outcome
