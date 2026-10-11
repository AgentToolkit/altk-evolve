# Functional Testing for Evolve Lite Skills

## Overview

Functional testing validates that skills actually work by simulating their execution in a mock environment. Unlike integration tests (which analyze completed conversations) or unit tests (which check trigger matching), functional tests answer the question: **"If I follow this skill's instructions, will it do what I expect?"**

## How It Works

### 1. Mock Environment

The test framework creates a mock environment that simulates:
- File system operations (creating, reading files)
- Command execution (with simulated outputs)
- Environment variables
- Error tracking

### 2. Step Extraction

The framework parses skill content to extract executable steps:
- Numbered steps (1., 2., 3.)
- Commands in backticks or code blocks
- Action descriptions

### 3. Execution Simulation

Each extracted step is "executed" in the mock environment:
- Commands are simulated with realistic outputs
- File operations are tracked
- Errors are captured

### 4. Outcome Validation

The test validates that:
- All steps were executed
- All steps succeeded
- Expected commands were run
- Expected files were created
- No errors occurred

## Running Functional Tests

### Basic Usage

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_skill_functional_tests.py
```

### With Verbose Output

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_skill_functional_tests.py --verbose
```

### Custom Scenarios Directory

```bash
python3 .bob/skills/evolve-lite-test/scripts/run_skill_functional_tests.py \
  --scenarios-dir /path/to/scenarios \
  --output /path/to/results
```

## Test Scenario Format

Scenarios are JSON files in `.evolve/tests/functional/scenarios/`:

```json
{
  "scenario_id": "test_orchestrate_agent_creation",
  "description": "Test that the skill for creating Watson Orchestrate agents actually works",
  "user_request": "Create a Watson Orchestrate agent",
  "expected_skills": [
    "to-create-and-upload-a-watson-orchestrate-agent-1-create-a"
  ],
  "setup": {
    "files": {
      "agent_config.json": "{\"name\": \"test-agent\"}"
    }
  },
  "expected_commands": [
    "orchestrate agents import"
  ],
  "success_criteria": {
    "required_files": [],
    "commands_must_succeed": true,
    "max_errors": 0
  }
}
```

### Scenario Fields

- **scenario_id**: Unique identifier for the test
- **description**: Human-readable description
- **user_request**: The user's original request
- **expected_skills**: List of skill slugs to test
- **setup**: Initial environment state
  - **files**: Files to create before execution
- **expected_commands**: Commands that should be executed
- **success_criteria**: Validation criteria
  - **required_files**: Files that must be created
  - **commands_must_succeed**: Whether commands must succeed
  - **max_errors**: Maximum allowed errors

## Test Results

Results are saved to `.evolve/tests/functional/results/`:

### Summary Report

`functional_test_report.json` contains:
- Total tests run
- Pass/fail counts
- Pass rate
- Individual test results

### Individual Test Results

Each test creates a detailed JSON file with:
- Skill information
- Execution log (steps found, executed, successful)
- Environment state (files created, commands run, errors)
- Validation results (what passed/failed)

## What Functional Tests Reveal

### 1. Incomplete Skills

**Example**: The `activate-the-virtual-environment` skill only has a title and rationale, but no actual steps or commands.

**Test Result**:
```json
{
  "steps_found": 1,
  "steps_executed": 1,
  "commands_executed": 0,
  "expected_commands_run": false,
  "passed": false
}
```

**Insight**: This skill needs more detailed instructions with actual commands.

### 2. Poor Step Extraction

**Example**: The `to-create-and-upload-a-watson-orchestrate-agent-1-create-a` skill has all steps in one sentence.

**Test Result**:
```json
{
  "steps_found": 1,
  "steps_executed": 1,
  "commands_executed": 1,
  "expected_commands_run": false,
  "passed": false
}
```

**Insight**: The skill format makes it hard to extract individual steps. Only the first command was found and executed, but the expected command (`orchestrate agents import`) was never run.

### 3. Missing Commands

When a skill describes actions but doesn't include executable commands, the test will show:
- Steps found but not executed
- No commands run
- Expected outcomes not achieved

### 4. Incorrect Command Syntax

If a skill includes commands with incorrect syntax or missing parameters, the mock environment will simulate failure and track the error.

## Best Practices for Testable Skills

### 1. Use Clear Step Numbering

**Good**:
```markdown
1. Create a YAML file with the agent configuration
2. Activate the virtual environment with `source .venv/bin/activate`
3. Import the agent with `orchestrate agents import --file agent.yaml`
```

**Bad**:
```markdown
To create an agent: 1) Create a YAML file... 2) Activate the virtual environment... 3) Import the agent...
```

### 2. Include Executable Commands

**Good**:
```markdown
Run the following command:
```bash
orchestrate agents import --file agent.yaml
```
```

**Bad**:
```markdown
Import the agent using the orchestrate CLI
```

### 3. Use Code Blocks for Multi-Line Commands

**Good**:
````markdown
```bash
source .venv/bin/activate
orchestrate env activate wxo699
orchestrate agents import --file agent.yaml
```
````

**Bad**:
```markdown
Run `source .venv/bin/activate` and then `orchestrate env activate wxo699` and finally `orchestrate agents import --file agent.yaml`
```

### 4. Separate Steps Clearly

Each step should be on its own line or in its own numbered section, not combined into one long sentence.

## Interpreting Test Results

### All Tests Passing

✅ Skills have clear, executable steps
✅ Commands are properly formatted
✅ Expected outcomes are achieved

### Tests Failing

❌ **Steps not found**: Skill content doesn't have clear numbered steps or commands
❌ **Commands not executed**: Commands aren't in backticks or code blocks
❌ **Expected commands not run**: The skill mentions different commands than what's expected
❌ **Errors occurred**: Commands failed in the mock environment

## Current Test Results

As of the latest run:

```
Total tests: 2
Passed: 0
Failed: 2
Pass rate: 0.0%
```

### Why Tests Are Failing

1. **activate-the-virtual-environment**: Skill has no executable commands
   - Only has a title and rationale
   - No steps or commands to extract
   - Needs detailed instructions added

2. **to-create-and-upload-a-watson-orchestrate-agent-1-create-a**: Poor step format
   - All steps in one sentence
   - Only first command extracted
   - Expected command never executed
   - Needs reformatting with clear numbered steps

## Next Steps

### 1. Improve Skill Content

Update skills to have:
- Clear numbered steps
- Commands in backticks or code blocks
- Separate lines for each step

### 2. Add More Test Scenarios

Create scenarios for:
- Error handling skills
- Multi-step workflows
- File creation skills
- Authentication skills

### 3. Enhance Mock Environment

Add simulation for:
- Network requests
- API calls
- Database operations
- More complex command outputs

### 4. Create Skill Templates

Provide templates that make it easy to write testable skills from the start.

## Comparison with Other Test Types

| Test Type | What It Tests | When to Use |
|-----------|---------------|-------------|
| **Integration** | Did skills get recalled and used in real conversations? | After completing tasks |
| **Unit** | Would the right skill be recalled for a given question? | When creating new skills |
| **Functional** | Do the skill's instructions actually work? | When writing skill content |

## Example: Creating a Testable Skill

### Before (Not Testable)

```markdown
---
type: atomic-skill
trigger: When deploying applications
---

Deploy the application

## Rationale

Standard deployment workflow
```

### After (Testable)

```markdown
---
type: atomic-skill
trigger: When deploying applications
---

Deploy the application

## Rationale

Standard deployment workflow for Python applications

## Steps

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
python main.py
```

3. Verify deployment:
```bash
curl http://localhost:8000/health
```
```

### Test Scenario

```json
{
  "scenario_id": "test_app_deployment",
  "description": "Test application deployment workflow",
  "user_request": "Deploy the application",
  "expected_skills": ["deploy-application"],
  "setup": {
    "files": {
      "requirements.txt": "flask==2.0.0",
      "main.py": "from flask import Flask\napp = Flask(__name__)"
    }
  },
  "expected_commands": [
    "pip install",
    "python main.py"
  ],
  "success_criteria": {
    "required_files": [],
    "commands_must_succeed": true,
    "max_errors": 0
  }
}
```

## Conclusion

Functional testing reveals whether skills are written in a way that makes them executable. It's not about whether the skill was recalled or whether it helped complete a task - it's about whether following the skill's instructions step-by-step would actually work.

The current test results show that many skills need better formatting and more detailed instructions to be truly functional. This is valuable feedback that helps improve skill quality.