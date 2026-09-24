---
name: evolve-lite:test
description: Generate test cases for skills based on conversation trajectories, validating that skills can be successfully applied to similar scenarios.
---

# Test Case Generator

## Invocation

To use this skill, you can invoke it in two ways:

### 1. Generate Test Cases
```
Use the evolve-lite:test skill to generate test cases for all trajectories
```

### 2. Run Test Cases
```
Use the evolve-lite:test skill to run test cases and generate a report
```

The skill will automatically determine whether to generate or run tests based on context.

## Overview

This skill analyzes conversation trajectories and generates test cases for each skill (guideline, atomic-skill, skill-flow) that was learned from those trajectories. Test cases validate that:

1. **Skills are discoverable**: The trigger matches appropriate scenarios
2. **Skills are actionable**: The content provides clear, executable guidance
3. **Skills are complete**: All necessary steps and context are included
4. **Skills compose correctly**: Skill-flows properly reference their atomic skills

## When To Use

Use this skill to:
- Generate test cases after learning new skills from trajectories
- Validate existing skills against new trajectories
- Build a regression test suite for the skill library
- Identify gaps or ambiguities in skill definitions

## Test Case Structure

Each test case includes:

```json
{
  "test_id": "unique-test-identifier",
  "skill_type": "guideline|atomic-skill|skill-flow",
  "skill_path": ".evolve/entities/skill-flow/example.md",
  "scenario": "Description of the test scenario",
  "expected_trigger_match": true,
  "input_context": {
    "user_request": "Original user request from trajectory",
    "environment": "Relevant environment details",
    "preconditions": ["List of preconditions"]
  },
  "expected_outcome": {
    "should_recall": true,
    "should_apply": true,
    "validation_criteria": ["List of success criteria"]
  },
  "trajectory_source": ".evolve/trajectories/trajectory_xxx.json",
  "created_at": "2026-06-18T18:00:00Z"
}
```

## Workflow

### Step 1: Select Trajectories and Skills

Choose which trajectories and skills to generate tests for:

```bash
# List available trajectories
ls -lt .evolve/trajectories/

# List available skills
ls -R .evolve/entities/
```

You can:
- Generate tests for all skills from a specific trajectory
- Generate tests for a specific skill across all trajectories
- Generate a full test suite for all skills

### Step 2: Generate Test Cases

Run the test case generator:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_test_cases.py \
  --trajectory .evolve/trajectories/trajectory_xxx.json \
  --output .evolve/tests/
```

Or generate for all trajectories:

```bash
python3 .bob/skills/evolve-lite-test/scripts/generate_test_cases.py \
  --all-trajectories \
  --output .evolve/tests/
```

The script will:
1. Load the trajectory and extract the conversation flow
2. Identify which skills were learned from this trajectory (via trajectory field)
3. For each skill, generate test cases based on:
   - The original user request that led to the skill
   - The context and environment from the trajectory
   - The successful outcome that validated the skill
4. Save test cases as JSON files in `.evolve/tests/`

### Step 3: Review Generated Test Cases

Examine the generated test cases:

```bash
cat .evolve/tests/test_*.json | python3 -m json.tool
```

Each test case should:
- Have a clear scenario description
- Include realistic input context
- Define measurable validation criteria
- Reference the source trajectory for provenance

### Step 4: Run Test Cases (Optional)

Validate skills against test cases:

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_test_cases.py \
  --test-dir .evolve/tests/ \
  --report .evolve/tests/test_report.json
```

This will:
1. Load each test case
2. Simulate the scenario by checking if:
   - The skill trigger matches the scenario
   - The skill content addresses the scenario requirements
   - For skill-flows, all referenced atomic skills exist
3. Generate a test report with pass/fail results

## Test Case Types

### 1. Trigger Match Tests

Validates that skill triggers correctly match relevant scenarios:

```json
{
  "test_type": "trigger_match",
  "scenario": "User wants to create a Watson Orchestrate agent",
  "expected_trigger_match": true,
  "skill_trigger": "When creating or uploading Watson Orchestrate agents"
}
```

### 2. Rubric-Based Execution Tests

Validates that executing the skill produces the outcomes defined in its `success_rubric`. This is the **primary test type** for `atomic-skill` and `skill-flow` entities — validation criteria must come directly from the entity's rubric, not invented generically.

For each entity with a rubric, generate:
- **1 happy-path test**: normal conditions, all rubric criteria expected to pass
- **1–3 edge case tests**: each covers a realistic boundary or failure condition (e.g. missing dependency, malformed input, already-existing output, partial environment). Each edge case must reference the rubric criteria and explicitly state which criterion is expected to fail or behave differently under that condition.

```json
{
  "test_type": "rubric_execution",
  "scenario": "Executing the skill end-to-end and verifying its success rubric",
  "edge_case": false,
  "validation_criteria": [
    "exit code 0 after running the main command",
    "output file exists at the expected path",
    "no error lines appear in stdout"
  ]
}
```

```json
{
  "test_type": "rubric_execution",
  "scenario": "Skill is run when a required dependency is missing",
  "edge_case": true,
  "edge_condition": "missing dependency",
  "validation_criteria": [
    "exit code 0 after running the main command — expected to FAIL",
    "output file exists at the expected path — not applicable",
    "no error lines appear in stdout — expected to FAIL with clear error message"
  ]
}
```

The `validation_criteria` array must be a verbatim or lightly paraphrased copy of the rubric items from the entity's `## Success Rubric` section. Do not substitute generic completeness checks when a rubric is present.

### 3. Content Completeness Tests

Validates that skill content provides sufficient guidance. Use this only when the entity has **no `success_rubric`** (e.g. legacy entities predating the rubric requirement):

```json
{
  "test_type": "content_completeness",
  "scenario": "Following the skill to complete the task",
  "validation_criteria": [
    "All required steps are present",
    "Commands are executable",
    "Prerequisites are stated",
    "Expected outcomes are clear"
  ]
}
```

### 4. Skill Composition Tests

Validates that skill-flows properly reference atomic skills:

```json
{
  "test_type": "skill_composition",
  "scenario": "Skill-flow references valid atomic skills",
  "validation_criteria": [
    "All atomic_skills exist in entities/atomic-skill/",
    "Atomic skill content matches flow steps",
    "No circular dependencies"
  ]
}
```

### 5. Trajectory Replay Tests

Validates that applying the skill to the original trajectory would succeed:

```json
{
  "test_type": "trajectory_replay",
  "scenario": "Replaying the original trajectory with the skill",
  "validation_criteria": [
    "Skill would be recalled at the right time",
    "Skill guidance matches what was done",
    "Outcome would be the same"
  ]
}
```

## Test Generation Strategies

### Strategy 1: Positive Tests from Success

For each skill learned from a trajectory:
- Extract the successful scenario that led to the skill
- Create a test case that validates the skill applies to that scenario
- Use the actual outcome as the expected result

### Strategy 2: Edge Case Tests from Rubric

For each skill with a `success_rubric`, generate 1–3 edge case tests alongside the happy-path test:
- Identify realistic boundary conditions: missing tools, malformed inputs, already-existing outputs, partial environments, or permission issues
- For each edge case, copy the rubric criteria into `validation_criteria` and annotate which are expected to fail or behave differently under that condition
- Use `edge_case: true` and an `edge_condition` description to distinguish from the happy-path test

### Strategy 3: Negative Tests from Failures

For skills that prevent errors:
- Extract the failure scenario from the trajectory
- Create a test case that validates the skill would prevent the error
- Use the error avoidance as the expected result

### Strategy 4: Variation Tests

For each skill:
- Generate variations of the original scenario
- Test that the skill trigger still matches
- Validate that the skill content is general enough

### Strategy 5: Composition Tests

For skill-flows:
- Test that all atomic skills are present
- Test that atomic skills can be executed in sequence
- Test that the composition achieves the flow's goal

## A/B Comparison Testing

### Overview

Compare task performance with skills (recalled and applied) versus without skills (baseline) to validate that skills actually improve outcomes.

### Metrics Compared

- **Tool uses**: Number of tool invocations required
- **Errors**: Number of errors encountered
- **Retries**: Number of retry attempts
- **User interventions**: Number of times user had to intervene
- **Completion status**: Whether task completed successfully

### Running Comparisons

```bash
python3 .bob/skills/evolve-lite-test/scripts/compare_with_without_skills.py \
  --with-skills .evolve/trajectories/trajectory_with_skills.json \
  --without-skills .evolve/trajectories/trajectory_without_skills.json \
  --output .evolve/tests/comparison_report.json
```

### Comparison Report Structure

```json
{
  "generated_at": "2026-06-18T19:30:00Z",
  "total_comparisons": 1,
  "comparisons": [{
    "with_skills": {
      "metrics": {
        "tool_uses": 15,
        "errors": 1,
        "retries": 2,
        "completion_status": "completed"
      }
    },
    "without_skills": {
      "metrics": {
        "tool_uses": 22,
        "errors": 3,
        "retries": 5,
        "completion_status": "completed"
      }
    },
    "improvements": {
      "fewer_tool_uses": 7,
      "fewer_errors": 2,
      "fewer_retries": 3
    },
    "summary": {
      "skills_helped": true,
      "efficiency_gain_percent": 31.8,
      "error_reduction_percent": 66.7
    }
  }],
  "aggregate_metrics": {
    "total_with_skills_helped": 1,
    "average_tool_use_reduction": 31.8,
    "average_error_reduction": 66.7
  }
}
```

### Creating Comparison Pairs

To create valid comparison pairs:

1. **Identify a task**: Choose a task that can be repeated
2. **Run without skills**: Complete the task without using evolve-lite:recall
3. **Save baseline trajectory**: Save the trajectory as `trajectory_baseline.json`
4. **Run with skills**: Complete the same task using evolve-lite:recall
5. **Save skills trajectory**: Save the trajectory as `trajectory_with_skills.json`
6. **Compare**: Run the comparison script

### Interpretation

- **Positive improvements**: Skills reduced tool uses, errors, or retries
- **Negative improvements**: Skills added overhead without benefit
- **No difference**: Skills had no measurable impact

Use comparison results to:
- Validate skill effectiveness
- Identify skills that need refinement
- Prioritize skill development efforts
- Demonstrate ROI of the skill library

## Best Practices

1. **Generate tests immediately after learning**: Create test cases when skills are fresh
2. **One test per skill per trajectory**: Each skill-trajectory pair gets one primary test
3. **Include negative tests**: Test that skills don't match irrelevant scenarios
4. **Test skill evolution**: When skills are updated, regenerate tests
5. **Maintain test provenance**: Always link tests back to source trajectories
6. **Review generated tests**: Human review ensures test quality
7. **Run tests periodically**: Validate skills haven't degraded over time

## Output Structure

```
.evolve/tests/
  test_skill-flow_create-and-upload-watson-agent_traj-2026-06-18.json
  test_atomic-skill_create-yaml-file_traj-2026-06-18.json
  test_guideline_use-context-managers_traj-2026-06-08.json
  test_report_2026-06-18T18-30-00.json
```

## Integration with Learn/Recall

The test mode complements the learn and recall skills:

- **Learn**: Extracts skills from trajectories → **Test**: Validates those skills
- **Recall**: Retrieves skills for tasks → **Test**: Ensures retrieved skills work
- **Test failures**: Indicate skills need refinement → **Learn**: Updates skills

This creates a continuous improvement loop for the skill library.