# Integration Testing for Evolve Lite Skills

## Overview

Integration tests validate that skills are **actually recalled and used** in real scenarios, not just that they're well-formed. Unlike static validation tests, integration tests analyze actual conversation trajectories to verify:

1. **Skills were recalled** - The `evolve-lite:recall` skill was used
2. **Expected skills were found** - Specific skills were mentioned/applied
3. **Outcome was successful** - Task completed within acceptable metrics
4. **Performance improved** - Fewer errors, tool uses, etc.

## Quick Start

### 1. Create a Test Scenario

Define what you expect to happen:

```json
{
  "scenario_id": "create_orchestrate_agent",
  "description": "Test Watson Orchestrate agent creation with skills",
  "user_request": "create a hello world agent in orchestrate",
  "expected_skills": [
    "to-create-and-upload-a-watson-orchestrate-agent-1-create-a"
  ],
  "success_criteria": {
    "completion_status": "completed",
    "max_tool_uses": 20,
    "max_errors": 2
  }
}
```

Save to: `.evolve/tests/integration/scenarios/your_scenario.json`

### 2. Execute the Scenario

**Option A: Manual execution**
1. Start a new Bob conversation
2. Use `evolve-lite:recall` at the start
3. Give the user request from the scenario
4. Save the trajectory when complete

**Option B: Automated execution** (future enhancement)
- Use Bob API to execute scenario programmatically

### 3. Run the Integration Test

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_integration_test.py \
  --scenario .evolve/tests/integration/scenarios/your_scenario.json \
  --trajectory .evolve/trajectories/your_trajectory.json \
  --verbose
```

### 4. Review Results

The test will output:
- ✅ **PASSED** if skills were recalled and criteria met
- ❌ **FAILED** if skills weren't used or criteria not met

Results are saved to: `.evolve/tests/integration/results/`

## Test Scenarios

### Scenario Structure

```json
{
  "scenario_id": "unique-identifier",
  "description": "What this scenario tests",
  "user_request": "The exact request to give Bob",
  "expected_skills": [
    "skill-slug-1",
    "skill-slug-2"
  ],
  "success_criteria": {
    "completion_status": "completed|failed|unknown",
    "max_tool_uses": 20,
    "max_errors": 2,
    "required_files": ["file1.txt", "file2.yaml"]
  },
  "notes": "Additional context about this test"
}
```

### Example Scenarios

**1. Watson Orchestrate Agent Creation**
```json
{
  "scenario_id": "create_orchestrate_agent",
  "user_request": "create a hello world agent in orchestrate",
  "expected_skills": [
    "to-create-and-upload-a-watson-orchestrate-agent-1-create-a"
  ],
  "success_criteria": {
    "completion_status": "completed",
    "max_tool_uses": 20,
    "max_errors": 2
  }
}
```

**2. Authentication Error Handling**
```json
{
  "scenario_id": "handle_auth_errors",
  "user_request": "import the agent but handle authentication errors",
  "expected_skills": [
    "when-the-watson-orchestrate-cli-reports-token-expired"
  ],
  "success_criteria": {
    "completion_status": "completed",
    "max_tool_uses": 15,
    "max_errors": 1
  }
}
```

## What Gets Validated

### 1. Skill Recall Detection

The test checks if:
- `evolve-lite:recall` was used in the conversation
- "Recall complete" messages appear
- Entity paths (`.evolve/entities/...`) are mentioned

### 2. Expected Skills Found

For each expected skill, checks if:
- The skill slug appears in the conversation
- The skill was mentioned in recall output
- The skill path was referenced

### 3. Success Criteria

Validates against defined criteria:
- **completion_status**: Did the task complete successfully?
- **max_tool_uses**: Was it efficient (not too many tool calls)?
- **max_errors**: Were errors kept to a minimum?
- **required_files**: Were expected files created?

### 4. Performance Metrics

Extracts and reports:
- Total tool uses
- Number of errors encountered
- Tools used during execution
- Estimated duration (if timestamps available)

## Test Results

### Result Structure

```json
{
  "test_id": "create_orchestrate_agent",
  "passed": true,
  "skill_analysis": {
    "skills_recalled": true,
    "recall_count": 1,
    "skills_mentioned": [
      ".evolve/entities/skill-flow/to-create-and-upload-a-watson-orchestrate-agent-1-create-a.md"
    ]
  },
  "metrics": {
    "tool_uses": 14,
    "errors": 1,
    "completion_status": "completed"
  },
  "validation": {
    "passed": true,
    "checks": {
      "skill_recalled_to-create-and-upload-a-watson-orchestrate-agent-1-create-a": true,
      "completion_status": true,
      "tool_uses_within_limit": true,
      "errors_within_limit": true
    },
    "failures": []
  }
}
```

### Understanding Results

**PASSED Test**:
- Skills were recalled at the start
- Expected skills were found in the conversation
- All success criteria were met
- Task completed successfully

**FAILED Test**:
- Skills were NOT recalled (forgot to use `evolve-lite:recall`)
- Expected skills were not mentioned (wrong skills recalled)
- Success criteria not met (too many errors, didn't complete)
- Required files not created

## Workflow Examples

### Testing a New Skill

1. **Learn the skill** from a successful trajectory
2. **Create a test scenario** for that skill
3. **Execute the scenario** with recall enabled
4. **Run the integration test** to verify
5. **Iterate** if the test fails

### Regression Testing

1. **Generate scenarios** for all existing skills
2. **Run integration tests** periodically
3. **Identify degraded skills** (tests that start failing)
4. **Update skills** based on failures
5. **Re-run tests** to verify fixes

### A/B Comparison

1. **Run scenario WITHOUT recall** (baseline)
2. **Run scenario WITH recall** (with skills)
3. **Compare trajectories** using `compare_with_without_skills.py`
4. **Measure improvement** (fewer errors, faster completion)

## Best Practices

### 1. Create Realistic Scenarios

- Use actual user requests from real conversations
- Include edge cases and error conditions
- Test both happy path and failure scenarios

### 2. Set Reasonable Criteria

- Don't make criteria too strict (allow some flexibility)
- Focus on key metrics (completion, major errors)
- Adjust criteria based on actual performance

### 3. Test Regularly

- Run integration tests after learning new skills
- Include in CI/CD pipeline if possible
- Track pass rates over time

### 4. Document Failures

- When tests fail, understand why
- Update skills or scenarios as needed
- Keep notes on common failure patterns

### 5. Maintain Test Suite

- Remove obsolete scenarios
- Update scenarios when skills change
- Keep scenarios aligned with current skills

## Limitations

### Current Limitations

1. **Manual execution required** - Can't automatically execute scenarios yet
2. **Heuristic skill detection** - Relies on text matching for skill mentions
3. **No negative tests** - Doesn't test that wrong skills aren't recalled
4. **Limited metrics** - Could track more performance indicators

### Future Enhancements

1. **Automated scenario execution** - Use Bob API to run scenarios
2. **Semantic skill matching** - Use embeddings for better detection
3. **Negative test generation** - Test that irrelevant skills aren't used
4. **Performance benchmarking** - Track skill effectiveness over time
5. **Batch test runner** - Run multiple scenarios in sequence

## Troubleshooting

### Test Always Fails: "Skills not recalled"

**Problem**: `skills_recalled: false`

**Solution**: Make sure you used `evolve-lite:recall` at the start of the conversation

### Test Fails: "Expected skill not recalled"

**Problem**: Skill wasn't mentioned in the conversation

**Solutions**:
- Check if the skill trigger matches the scenario
- Verify the skill exists in `.evolve/entities/`
- Update the scenario to expect different skills

### Test Fails: "Too many tool uses"

**Problem**: `tool_uses > max_tool_uses`

**Solutions**:
- Increase `max_tool_uses` in success criteria
- Investigate why so many tools were needed
- Check if skills are actually helping efficiency

### Test Fails: "Completion status mismatch"

**Problem**: Task didn't complete as expected

**Solutions**:
- Check trajectory for errors or incomplete work
- Verify the scenario is achievable
- Update success criteria if needed

## Examples

See example scenarios in:
- `.evolve/tests/integration/scenarios/create_orchestrate_agent.json`
- `.evolve/tests/integration/scenarios/handle_auth_errors.json`

See example results in:
- `.evolve/tests/integration/results/`

---

Made with Bob - Evolve Lite Integration Testing v1.0