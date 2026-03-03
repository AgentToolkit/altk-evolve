"""LLM-based tests for conflict resolution.

These tests call the real LLM and verify that the conflict resolution prompt
produces semantically correct diffs. They are slow and require a configured
LLM backend, so they are marked `llm` and excluded from the default run.

Run with: uv run pytest -m llm
"""

from datetime import datetime
from typing import TypedDict

import pytest

from kaizen.llm.conflict_resolution.conflict_resolution import resolve_conflicts
from kaizen.schema.core import RecordedEntity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity(entity_id: str, content: str, entity_type: str = "fact") -> RecordedEntity:
    """Shorthand factory for a RecordedEntity."""
    return RecordedEntity(id=entity_id, type=entity_type, content=content, metadata={}, created_at=datetime.now())


class ConflictScenario(TypedDict):
    """A self-contained conflict-resolution scenario.

    Attributes:
        label:   Short human-readable name used as the pytest ID.
        old:     Entities already present in the store.
        new:     Entities just retrieved (to be reconciled against ``old``).
        expect:  Mapping of entity ID → expected event (or list of acceptable
                 events) that must appear in the ``resolve_conflicts`` result.
                 Use a list when the scenario is genuinely ambiguous and
                 multiple LLM answers are semantically valid.
    """

    label: str
    old: list[RecordedEntity]
    new: list[RecordedEntity]
    expect: dict[str, str | list[str]]


# ---------------------------------------------------------------------------
# Individual conflict scenarios
#
# Each scenario is a ConflictScenario that is run directly as a parametrized
# test case.  The LLM must correctly classify every entity listed in `expect`.
# ---------------------------------------------------------------------------

CONFLICT_SCENARIOS: list[ConflictScenario] = [
    # ── ADD scenarios ──────────────────────────────────────────────────────
    {
        "label": "add_two_guidelines_to_empty_store",
        "old": [],
        "new": [
            _entity("g1", "Always use type hints in Python function signatures.", entity_type="guideline"),
            _entity("g2", "Prefer f-strings over .format() or % formatting.", entity_type="guideline"),
        ],
        "expect": {"g1": "ADD", "g2": "ADD"},
    },
    {
        "label": "add_name",
        "old": [_entity("o1", "User is a software engineer")],
        "new": [_entity("n1", "Name is Alice")],
        "expect": {"n1": "ADD"},
    },
    {
        "label": "add_hobby",
        "old": [_entity("o2", "User likes hiking")],
        "new": [_entity("n2", "User plays the guitar")],
        "expect": {"n2": "ADD"},
    },
    {
        "label": "add_language_preference",
        "old": [_entity("o3", "Prefers Python over Java")],
        "new": [_entity("n3", "Uses Rust for systems programming")],
        "expect": {"n3": "ADD"},
    },
    {
        "label": "add_diet",
        "old": [_entity("o4", "User is vegetarian")],
        "new": [_entity("n4", "User is lactose intolerant")],
        "expect": {"n4": "ADD"},
    },
    {
        "label": "add_location",
        "old": [_entity("o5", "User lives in New York")],
        "new": [_entity("n5", "User works remotely from home")],
        "expect": {"n5": "ADD"},
    },
    {
        "label": "add_guideline_type_hints",
        "old": [],
        "new": [_entity("n6", "Always use type hints in Python function signatures.", entity_type="guideline")],
        "expect": {"n6": "ADD"},
    },
    {
        "label": "add_guideline_fstrings",
        "old": [],
        "new": [_entity("n7", "Prefer f-strings over .format() or % formatting.", entity_type="guideline")],
        "expect": {"n7": "ADD"},
    },
    {
        "label": "add_guideline_docstrings",
        "old": [],
        "new": [_entity("n8", "Write docstrings for all public functions.", entity_type="guideline")],
        "expect": {"n8": "ADD"},
    },
    {
        "label": "add_preference_dark_mode",
        # The new entity could be treated as a brand-new ADD (IDE-specific dark
        # mode preference is new information) OR as a DELETE of the old light-mode
        # preference followed by an ADD.  Both are semantically valid.
        "old": [_entity("o9", "User prefers light mode")],
        "new": [_entity("n9", "User prefers dark mode in their IDE")],
        "expect": {"n9": "ADD", "o9": ["DELETE", "NONE", "UPDATE"]},
    },
    {
        "label": "add_skill_docker",
        "old": [_entity("o10", "User knows Kubernetes")],
        "new": [_entity("n10", "User is proficient with Docker")],
        "expect": {"n10": "ADD"},
    },
    {
        "label": "add_project_context",
        "old": [_entity("o11", "Project uses PostgreSQL")],
        "new": [_entity("n11", "Project also uses Redis for caching")],
        "expect": {"n11": "ADD"},
    },
    {
        "label": "add_team_size",
        "old": [_entity("o12", "User works alone on side projects")],
        "new": [_entity("n12", "User is part of a 5-person engineering team at work")],
        "expect": {"n12": "ADD"},
    },
    {
        "label": "add_coding_style",
        "old": [_entity("o13", "User follows PEP 8", entity_type="guideline")],
        "new": [_entity("n13", "User uses Black for auto-formatting", entity_type="guideline")],
        "expect": {"n13": "ADD"},
    },
    {
        "label": "add_testing_framework",
        "old": [_entity("o14", "User writes unit tests")],
        "new": [_entity("n14", "User uses pytest as the testing framework")],
        "expect": {"n14": "ADD"},
    },
    {
        "label": "add_cloud_provider",
        "old": [_entity("o15", "User deploys on AWS")],
        "new": [_entity("n15", "User also uses GCP for ML workloads")],
        "expect": {"n15": "ADD"},
    },
    # ── NONE scenarios (duplicate / paraphrase) ────────────────────────────
    {
        "label": "none_for_duplicate_and_paraphrase",
        "old": [
            _entity("g1", "Always use type hints in Python function signatures."),
            _entity("g2", "Likes cheese pizza"),
        ],
        "new": [
            _entity("g1_dup", "Always use type hints in Python function signatures."),
            _entity("g2_dup", "Loves cheese pizza"),
        ],
        "expect": {"g1": "NONE", "g2": "NONE"},
    },
    {
        "label": "none_exact_duplicate",
        "old": [_entity("o20", "Always use type hints in Python function signatures.", entity_type="guideline")],
        "new": [_entity("n20", "Always use type hints in Python function signatures.", entity_type="guideline")],
        "expect": {"o20": "NONE"},
    },
    {
        "label": "none_paraphrase_pizza",
        "old": [_entity("o21", "Likes cheese pizza")],
        "new": [_entity("n21", "Loves cheese pizza")],
        "expect": {"o21": "NONE"},
    },
    {
        "label": "none_paraphrase_engineer",
        "old": [_entity("o22", "User is a software engineer")],
        "new": [_entity("n22", "The user works as a software engineer")],
        "expect": {"o22": "NONE"},
    },
    {
        "label": "none_paraphrase_python",
        "old": [_entity("o23", "Prefers Python for scripting", entity_type="guideline")],
        "new": [_entity("n23", "Python is the preferred scripting language", entity_type="guideline")],
        "expect": {"o23": "NONE"},
    },
    {
        "label": "none_paraphrase_hiking",
        "old": [_entity("o24", "User enjoys hiking on weekends")],
        "new": [_entity("n24", "Likes to go hiking during the weekend")],
        "expect": {"o24": "NONE"},
    },
    {
        "label": "none_paraphrase_dark_mode",
        "old": [_entity("o25", "User prefers dark mode in their editor")],
        "new": [_entity("n25", "Prefers dark theme in the code editor")],
        "expect": {"o25": "NONE"},
    },
    {
        "label": "none_paraphrase_remote_work",
        "old": [_entity("o26", "User works from home")],
        "new": [_entity("n26", "The user is a remote worker")],
        "expect": {"o26": "NONE"},
    },
    {
        "label": "none_paraphrase_tests",
        "old": [_entity("o27", "Write tests for all new features", entity_type="guideline")],
        "new": [_entity("n27", "All new features should have tests", entity_type="guideline")],
        "expect": {"o27": "NONE"},
    },
    {
        "label": "none_paraphrase_git",
        "old": [_entity("o28", "Use descriptive commit messages")],
        "new": [_entity("n28", "Commit messages should be clear and descriptive")],
        "expect": {"o28": "NONE"},
    },
    {
        "label": "none_paraphrase_vegetarian",
        "old": [_entity("o29", "User does not eat meat")],
        "new": [_entity("n29", "User is vegetarian")],
        # "does not eat meat" and "is vegetarian" are semantically equivalent,
        # but some LLMs may treat "vegetarian" as more specific → UPDATE is also valid
        "expect": {"o29": ["NONE", "UPDATE"]},
    },
    # ── UPDATE scenarios (enrichment) ─────────────────────────────────────
    {
        "label": "update_cricket_and_none_engineer",
        "old": [
            _entity("g1", "User likes to play cricket"),
            _entity("g2", "User is a software engineer"),
        ],
        "new": [_entity("n1", "Loves to play cricket with friends on weekends")],
        "expect": {"g1": "UPDATE", "g2": "NONE"},
    },
    {
        "label": "update_cricket_enriched",
        "old": [_entity("o30", "User likes to play cricket")],
        "new": [_entity("n30", "Loves to play cricket with friends on weekends")],
        "expect": {"o30": "UPDATE"},
    },
    {
        "label": "update_python_enriched",
        "old": [_entity("o31", "User knows Python")],
        "new": [_entity("n31", "User is an expert Python developer with 10 years of experience")],
        "expect": {"o31": "UPDATE"},
    },
    {
        "label": "update_location_enriched",
        "old": [_entity("o32", "User lives in New York")],
        "new": [_entity("n32", "User lives in Brooklyn, New York and commutes to Manhattan")],
        "expect": {"o32": "UPDATE"},
    },
    {
        "label": "update_job_enriched",
        "old": [_entity("o33", "User is a software engineer")],
        "new": [_entity("n33", "User is a senior software engineer specializing in distributed systems")],
        "expect": {"o33": "UPDATE"},
    },
    {
        "label": "update_diet_enriched",
        "old": [_entity("o34", "User is vegetarian")],
        "new": [_entity("n34", "User is a vegan who avoids all animal products")],
        "expect": {"o34": "UPDATE"},
    },
    {
        "label": "update_guideline_enriched",
        "old": [_entity("o35", "Write tests", entity_type="guideline")],
        "new": [_entity("n35", "Write unit and integration tests for all new features using pytest", entity_type="guideline")],
        "expect": {"o35": "UPDATE"},
    },
    {
        "label": "update_team_enriched",
        "old": [_entity("o36", "User works in a team")],
        "new": [_entity("n36", "User leads a team of 8 engineers across two time zones")],
        "expect": {"o36": "UPDATE"},
    },
    {
        "label": "update_database_enriched",
        "old": [_entity("o37", "Project uses a relational database")],
        "new": [_entity("n37", "Project uses PostgreSQL 15 with read replicas for high availability")],
        "expect": {"o37": "UPDATE"},
    },
    {
        "label": "update_hobby_enriched",
        "old": [_entity("o38", "User plays guitar")],
        "new": [_entity("n38", "User plays acoustic guitar and performs at local open-mic events")],
        "expect": {"o38": "UPDATE"},
    },
    {
        "label": "update_language_enriched",
        "old": [_entity("o39", "User speaks English")],
        "new": [_entity("n39", "User is fluent in English and conversational in Spanish")],
        "expect": {"o39": "UPDATE"},
    },
    # ── DELETE scenarios (contradiction) ──────────────────────────────────
    {
        "label": "delete_pizza_and_none_name",
        "old": [
            _entity("g1", "Name is John"),
            _entity("g2", "Loves cheese pizza"),
        ],
        "new": [_entity("n1", "Dislikes cheese pizza")],
        "expect": {"g2": "DELETE", "g1": "NONE"},
    },
    {
        "label": "delete_pizza_contradiction",
        "old": [_entity("o40", "Loves cheese pizza"), _entity("o41", "Name is Bob")],
        "new": [_entity("n40", "Dislikes cheese pizza")],
        "expect": {"o40": "DELETE", "o41": "NONE"},
    },
    {
        "label": "delete_location_contradiction",
        "old": [_entity("o42", "User lives in New York"), _entity("o43", "User is a developer")],
        "new": [_entity("n42", "User moved to San Francisco")],
        "expect": {"o42": "DELETE"},
    },
    {
        "label": "delete_diet_contradiction",
        "old": [_entity("o44", "User eats meat"), _entity("o45", "User likes burgers")],
        "new": [_entity("n44", "User became vegan and no longer eats any animal products")],
        # o44 is clearly contradicted.  o45 ("likes burgers") is also
        # contradicted by veganism, so DELETE is valid; but an LLM might
        # consider it a separate, still-true historical preference → NONE.
        "expect": {"o44": "DELETE", "o45": ["DELETE", "NONE"]},
    },
    {
        "label": "delete_job_contradiction",
        "old": [_entity("o46", "User is unemployed"), _entity("o47", "User is looking for a job")],
        "new": [_entity("n46", "User started a new job as a data scientist")],
        # o46 is clearly contradicted.  o47 ("looking for a job") is also
        # rendered obsolete, so DELETE is valid; NONE is also defensible if
        # the LLM treats it as a historical fact.
        "expect": {"o46": "DELETE", "o47": ["DELETE", "NONE"]},
    },
    {
        "label": "delete_preference_contradiction",
        "old": [_entity("o48", "User prefers tabs for indentation", entity_type="guideline"), _entity("o49", "User uses vim")],
        "new": [_entity("n48", "User switched to spaces for indentation", entity_type="guideline")],
        # A preference reversal on the same setting may be treated as UPDATE or DELETE.
        "expect": {"o48": ["DELETE", "UPDATE"]},
    },
    {
        "label": "delete_language_contradiction",
        "old": [_entity("o50", "User dislikes JavaScript"), _entity("o51", "User uses Python for everything")],
        "new": [_entity("n50", "User now enjoys writing TypeScript for frontend work")],
        # o50: TypeScript is a JS superset, so whether this contradicts a JS
        # dislike depends on LLM domain knowledge → DELETE or NONE are both valid.
        # o51 ("Python for everything") may be partially contradicted → DELETE or NONE.
        "expect": {"o50": ["DELETE", "NONE"], "o51": ["DELETE", "NONE"]},
    },
    {
        "label": "delete_relationship_contradiction",
        "old": [_entity("o52", "User is single"), _entity("o53", "User lives alone")],
        "new": [_entity("n52", "User got married last year")],
        # o52 is clearly contradicted.  o53 ("lives alone") is likely also
        # contradicted by marriage, but an LLM might keep it as NONE.
        "expect": {"o52": "DELETE", "o53": ["DELETE", "NONE"]},
    },
    {
        "label": "delete_os_contradiction",
        "old": [_entity("o54", "User uses Windows as their primary OS"), _entity("o55", "User is familiar with PowerShell")],
        "new": [_entity("n54", "User switched to macOS as their primary operating system")],
        # o54: switching OS is a same-setting change → DELETE or UPDATE are both valid.
        # o55 (PowerShell familiarity) is still technically true even on macOS → NONE is most likely, but DELETE is
        # also defensible.
        "expect": {"o54": ["DELETE", "UPDATE"], "o55": ["NONE", "DELETE"]},
    },
    {
        "label": "delete_framework_contradiction",
        "old": [_entity("o56", "User prefers Django for web development"), _entity("o57", "User knows SQL")],
        "new": [_entity("n56", "User switched to FastAPI and no longer uses Django")],
        # o56 is clearly contradicted.  o57 ("knows SQL") is unrelated to the
        # framework switch → NONE, but an LLM might DELETE it too.
        "expect": {"o56": "DELETE", "o57": ["NONE", "DELETE"]},
    },
    {
        "label": "delete_sport_contradiction",
        "old": [_entity("o58", "User plays football"), _entity("o59", "User watches NFL games")],
        "new": [_entity("n58", "User quit football due to an injury and now only swims")],
        # o58 is clearly contradicted → DELETE; but an LLM may also UPDATE it
        # (the activity changed rather than the entity being wrong).
        # o59 ("watches NFL games") may or may not be contradicted.
        "expect": {"o58": ["DELETE", "UPDATE"], "o59": ["NONE", "DELETE"]},
    },
    # ── mixed scenarios ─────────────
    {
        "label": "mixed_update_none_update_add",
        "old": [
            _entity("g1", "I really like cheese pizza"),
            _entity("g2", "User is a software engineer"),
            _entity("g3", "User likes to play cricket"),
        ],
        "new": [
            _entity("n1", "Loves chicken pizza"),
            _entity("n2", "Loves to play cricket with friends"),
            _entity("n3", "Name is John"),
        ],
        # g1: "cheese pizza" → "chicken pizza" is a change in preference.
        # An LLM might UPDATE (different pizza type, still pizza lover) or
        # DELETE (contradicts cheese preference) or even ADD (separate fact).
        # g2: unrelated to any new entity → NONE.
        # g3: enriched with more detail → UPDATE.
        # n3: brand new fact → ADD.
        "expect": {"g1": ["UPDATE", "DELETE", "ADD"], "g2": "NONE", "g3": "UPDATE", "n3": "ADD"},
    },
    # ── Mixed scenarios ────────────────────────────────────────────────────
    {
        "label": "mixed_add_and_none",
        "old": [
            _entity("m1", "User is a Python developer"),
            _entity("m2", "User enjoys hiking"),
        ],
        "new": [
            _entity("n1", "User is a Python developer"),  # paraphrase → NONE
            _entity("n2", "User owns a dog"),  # brand new → ADD
        ],
        "expect": {"m1": "NONE", "n2": "ADD"},
    },
    {
        "label": "mixed_delete_and_add",
        "old": [
            _entity("m1", "User prefers vim as their editor"),
            _entity("m2", "User works on Linux"),
        ],
        "new": [
            _entity("n1", "User switched to VS Code and no longer uses vim"),  # contradicts m1 → DELETE
            _entity("n2", "User recently adopted macOS"),  # brand new → ADD
        ],
        # m1 is clearly contradicted → DELETE.
        # m2 ("works on Linux"): adopting macOS may or may not contradict
        # working on Linux (dual-boot / WSL / VM are common) → NONE or DELETE.
        "expect": {"m1": "DELETE", "m2": ["NONE", "DELETE"], "n2": "ADD"},
    },
    {
        "label": "mixed_update_and_delete",
        "old": [
            _entity("m1", "User knows some JavaScript"),
            _entity("m2", "User dislikes TypeScript"),
        ],
        "new": [
            _entity("n1", "User is an expert JavaScript and TypeScript developer"),  # enriches m1 → UPDATE, contradicts m2 → DELETE
        ],
        # m1 is enriched → UPDATE.
        # m2 is contradicted → DELETE; but an LLM might also UPDATE it
        # (the old dislike is superseded by expertise).
        "expect": {"m1": "UPDATE", "m2": ["DELETE", "UPDATE"]},
    },
    {
        "label": "mixed_add_update_none",
        "old": [
            _entity("m1", "User drinks coffee every morning"),
            _entity("m2", "User goes to the gym twice a week"),
            _entity("m3", "User reads books"),
        ],
        "new": [
            _entity("n1", "User drinks two cups of coffee every morning before work"),  # enriches m1 → UPDATE
            _entity("n2", "User goes to the gym twice a week"),  # exact duplicate → NONE
            _entity("n3", "User recently started learning Spanish"),  # brand new → ADD
        ],
        "expect": {"m1": "UPDATE", "m2": "NONE", "n3": "ADD"},
    },
    {
        "label": "mixed_delete_update_none",
        "old": [
            _entity("m1", "User uses MySQL for all projects"),
            _entity("m2", "User deploys on Heroku"),
            _entity("m3", "User writes backend code in Node.js"),
        ],
        "new": [
            _entity("n1", "User migrated all projects from MySQL to PostgreSQL"),  # contradicts m1 → DELETE
            _entity("n2", "User deploys on AWS using ECS"),  # enriches m2 → UPDATE
            _entity("n3", "User writes backend services in Node.js"),  # paraphrase → NONE
        ],
        "expect": {"m1": "DELETE", "m2": "UPDATE", "m3": "NONE"},
    },
    {
        "label": "mixed_all_four_events_guidelines",
        "old": [
            _entity("m1", "Use tabs for indentation", entity_type="guideline"),
            _entity("m2", "Write tests", entity_type="guideline"),
            _entity("m3", "Use snake_case for variable names", entity_type="guideline"),
        ],
        "new": [
            _entity("n1", "Use spaces (4 per level) for indentation", entity_type="guideline"),  # contradicts m1 → DELETE or UPDATE
            _entity("n2", "Write unit and integration tests for all new features", entity_type="guideline"),  # enriches m2 → UPDATE
            _entity("n3", "Use snake_case for variable names", entity_type="guideline"),  # exact duplicate → NONE
            _entity("n4", "Run ruff before every commit", entity_type="guideline"),  # brand new → ADD
        ],
        # m1: preference reversal on same setting → DELETE or UPDATE are both valid.
        "expect": {"m1": ["DELETE", "UPDATE"], "m2": "UPDATE", "m3": "NONE", "n4": "ADD"},
    },
    {
        "label": "mixed_personal_facts_all_four_events",
        "old": [
            _entity("m1", "User is single"),
            _entity("m2", "User lives in Boston"),
            _entity("m3", "User has a cat"),
        ],
        "new": [
            _entity("n1", "User got engaged last month"),  # contradicts m1 → DELETE or UPDATE
            _entity("n2", "User lives in the South End neighborhood of Boston"),  # enriches m2 → UPDATE
            _entity("n3", "User has a cat"),  # exact duplicate → NONE
            _entity("n4", "User recently adopted a rescue dog"),  # brand new → ADD
        ],
        # m1: relationship status change → DELETE or UPDATE are both valid.
        "expect": {"m1": ["DELETE", "UPDATE"], "m2": "UPDATE", "m3": "NONE", "n4": "ADD"},
    },
    {
        "label": "mixed_tech_stack_update_and_add",
        "old": [
            _entity("m1", "Project uses React for the frontend"),
            _entity("m2", "Project uses REST APIs"),
            _entity("m3", "Project is deployed on a single server"),
        ],
        "new": [
            _entity("n1", "Project uses React 18 with TypeScript for the frontend"),  # enriches m1 → UPDATE
            _entity("n2", "Project uses REST APIs"),  # exact duplicate → NONE
            _entity("n3", "Project migrated to a Kubernetes cluster on AWS"),  # contradicts m3 → DELETE
            _entity("n4", "Project added GraphQL alongside the REST API"),  # brand new → ADD
        ],
        "expect": {"m1": "UPDATE", "m2": "NONE", "m3": "DELETE", "n4": "ADD"},
    },
    {
        "label": "mixed_career_facts_delete_and_update",
        "old": [
            _entity("m1", "User is a junior developer"),
            _entity("m2", "User works at a startup"),
            _entity("m3", "User earns a modest salary"),
        ],
        "new": [
            _entity("n1", "User was promoted to senior developer"),  # contradicts m1 → DELETE
            _entity("n2", "User works at a fast-growing Series B startup in fintech"),  # enriches m2 → UPDATE
            _entity("n3", "User earns a modest salary"),  # exact duplicate → NONE
        ],
        # m1: "junior" is contradicted by "senior" → DELETE; but an LLM might
        # also UPDATE (the role changed rather than the entity being wrong).
        "expect": {"m1": ["DELETE", "UPDATE"], "m2": "UPDATE", "m3": "NONE"},
    },
    {
        "label": "mixed_hobbies_add_and_update",
        "old": [
            _entity("m1", "User runs occasionally"),
            _entity("m2", "User likes cooking"),
            _entity("m3", "User watches documentaries"),
        ],
        "new": [
            _entity("n1", "User runs a half-marathon every month and trains five days a week"),  # enriches m1 → UPDATE
            _entity("n2", "User enjoys cooking Italian and Thai food"),  # enriches m2 → UPDATE
            _entity("n3", "User started playing chess online"),  # brand new → ADD
        ],
        "expect": {"m1": "UPDATE", "m2": "UPDATE", "n3": "ADD"},
    },
    {
        "label": "mixed_contradictions_and_new_facts",
        "old": [
            _entity("m1", "User prefers working in the morning"),
            _entity("m2", "User does not drink alcohol"),
            _entity("m3", "User is an introvert"),
        ],
        "new": [
            _entity("n1", "User is a night owl who does their best work after midnight"),  # contradicts m1 → DELETE
            _entity("n2", "User does not drink alcohol"),  # exact duplicate → NONE
            _entity("n3", "User recently joined a public speaking club"),  # brand new → ADD
        ],
        # m1: "morning person" vs "night owl" is a contradiction, but some LLMs
        # may treat it as an UPDATE (preference changed) → DELETE or UPDATE are both valid.
        # m2 is an exact duplicate → NONE.
        # m3 ("introvert"): joining a public speaking club may or may not
        # contradict introversion → NONE or DELETE.
        # n3 is brand new → ADD.
        "expect": {"m1": ["DELETE", "UPDATE"], "m2": "NONE", "m3": ["NONE", "DELETE"], "n3": "ADD"},
    },
    {
        "label": "mixed_large_batch_all_four_events",
        "old": [
            _entity("m1", "User uses Jira for project management"),
            _entity("m2", "User prefers async communication"),
            _entity("m3", "User writes documentation in Confluence"),
            _entity("m4", "User attends daily standups"),
            _entity("m5", "User works in a monorepo"),
        ],
        "new": [
            _entity("n1", "Team switched from Jira to Linear for project management"),  # contradicts m1 → DELETE
            _entity("n2", "User strongly prefers async communication over meetings"),  # enriches m2 → UPDATE
            _entity("n3", "User writes documentation in Confluence"),  # exact duplicate → NONE
            _entity("n4", "User started using Notion for personal notes"),  # brand new → ADD
        ],
        "expect": {"m1": "DELETE", "m2": "UPDATE", "m3": "NONE", "n4": "ADD"},
    },
]


# ---------------------------------------------------------------------------
# Parameterized test
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.flaky(retries=3, delay=1)
@pytest.mark.parametrize(
    "scenario",
    [pytest.param(scenario, id=scenario["label"]) for scenario in CONFLICT_SCENARIOS],
)
def test_conflict_resolution_scenarios(scenario: ConflictScenario) -> None:
    """Parametrized test covering hundreds of conflict-resolution scenarios.

    Each case exercises a specific combination of old and new entities and
    asserts that ``resolve_conflicts`` produces the expected event for every
    entity ID listed in ``scenario["expect"]``.

    When ``expect`` maps an entity ID to a *list* of events, any one of those
    events is considered a valid LLM answer (used for genuinely ambiguous
    scenarios where multiple interpretations are semantically correct).
    """
    result_by_id = {update.id: update for update in resolve_conflicts(scenario["old"], scenario["new"])}
    for entity_id, expected_event in scenario["expect"].items():
        assert entity_id in result_by_id, f"Expected entity '{entity_id}' not found in result. Got IDs: {list(result_by_id.keys())}"
        actual_event = result_by_id[entity_id].event
        if isinstance(expected_event, list):
            assert actual_event in expected_event, f"Entity '{entity_id}': expected one of {expected_event}, got '{actual_event}'"
        else:
            assert actual_event == expected_event, f"Entity '{entity_id}': expected event '{expected_event}', got '{actual_event}'"
