# How Functional Test Evaluation Works

## Overview

The functional test framework evaluates skills through a **5-step validation process**. A skill must pass ALL 5 checks to be considered functional.

---

## The 5 Validation Checks

### 1. ✅ All Steps Executed
```python
validation["all_steps_executed"] = (steps_executed == steps_found)
```

**What it checks:** Were all extracted steps actually executed?

**Passes when:**
- Every step found in the skill was executed
- No steps were skipped

**Fails when:**
- Some steps couldn't be executed
- Step extraction failed

**Example Failure:**
```
Steps found: 4
Steps executed: 1
Result: ❌ FAIL - Only 1 of 4 steps executed
```

---

### 2. ✅ All Steps Successful
```python
validation["all_steps_successful"] = (steps_successful == steps_executed)
```

**What it checks:** Did all executed steps complete successfully?

**Passes when:**
- Every executed step returned success
- No commands failed
- No errors occurred during execution

**Fails when:**
- A command returned an error
- A step failed to complete

**Example Failure:**
```
Steps executed: 3
Steps successful: 2
Result: ❌ FAIL - Step 3 failed
```

---

### 3. ✅ No Errors
```python
validation["no_errors"] = (len(env.errors) == 0)
```

**What it checks:** Were there any errors during execution?

**Passes when:**
- No errors were logged
- All operations completed cleanly

**Fails when:**
- File not found errors
- Missing parameters
- Command execution errors

**Example Failure:**
```
Errors: ["File not found: agent.yaml"]
Result: ❌ FAIL - Errors occurred
```

---

### 4. ✅ Expected Files Created
```python
required_files = scenario["success_criteria"]["required_files"]
validation["expected_files_created"] = all(env.file_exists(f) for f in required_files)
```

**What it checks:** Were the expected output files created?

**Passes when:**
- All required files exist in the mock environment
- OR no files were required by the scenario

**Fails when:**
- A required file is missing
- File creation step was skipped

**Example Failure:**
```
Required files: ["output.txt", "config.json"]
Files created: ["output.txt"]
Result: ❌ FAIL - config.json not created
```

---

### 5. ✅ Expected Commands Run
```python
expected_commands = scenario["expected_commands"]
validation["expected_commands_run"] = all(
    any(exp in cmd for cmd in commands_run)
    for exp in expected_commands
)
```

**What it checks:** Were the expected commands actually executed?

**Passes when:**
- All expected commands were run
- OR no specific commands were required

**Fails when:**
- An expected command was never executed
- Wrong commands were run instead

**Example Failure:**
```
Expected: ["orchestrate agents import"]
Executed: ["orchestrate env activate"]
Result: ❌ FAIL - Expected command not run
```

---

## Overall Pass/Fail Logic

```python
validation["passed"] = all([
    validation["all_steps_executed"],      # Check 1
    validation["all_steps_successful"],    # Check 2
    validation["no_errors"],               # Check 3
    validation["expected_files_created"],  # Check 4
    validation["expected_commands_run"]    # Check 5
])
```

**A skill PASSES only if ALL 5 checks pass.**

**A skill FAILS if ANY check fails.**

---

## Real Example: Orchestrate Agent Skill

### Test Scenario
```json
{
  "expected_commands": ["orchestrate agents import"],
  "success_criteria": {
    "required_files": [],
    "commands_must_succeed": true,
    "max_errors": 0
  }
}
```

### Skill Content
```
To create and upload a Watson Orchestrate agent: 1) Create a YAML file... 
2) Activate the virtual environment. 3) Ensure the orchestrate environment 
is authenticated with `orchestrate env activate`. 4) Import the agent with 
`orchestrate agents import --file <agent.yaml>`.
```

### Step Extraction
```
Steps found: 1 (entire content treated as one step)
Commands extracted: 
  - orchestrate env activate (first command in backticks)
  - orchestrate agents import --file <agent.yaml> (second command)
```

### Execution
```
Step 1: Execute "orchestrate env activate"
  Result: ✅ SUCCESS - "Environment activated"
```

### Validation Results

| Check | Result | Reason |
|-------|--------|--------|
| 1. All steps executed | ✅ PASS | 1/1 steps executed |
| 2. All steps successful | ✅ PASS | 1/1 steps succeeded |
| 3. No errors | ✅ PASS | 0 errors |
| 4. Expected files created | ✅ PASS | No files required |
| 5. Expected commands run | ❌ **FAIL** | `orchestrate agents import` not executed |

**Overall: ❌ FAIL**

### Why It Failed

The skill content has 4 numbered steps, but they're all in one sentence. The step extractor treats this as ONE step and only extracts the FIRST command (`orchestrate env activate`). 

The expected command (`orchestrate agents import`) is never executed, so check #5 fails.

---

## Real Example: Virtual Environment Skill

### Test Scenario
```json
{
  "expected_commands": ["source .venv/bin/activate"],
  "success_criteria": {
    "required_files": [],
    "commands_must_succeed": true,
    "max_errors": 0
  }
}
```

### Skill Content
```
Activate the virtual environment
```

### Step Extraction
```
Steps found: 1
Commands extracted: (none - no backticks or code blocks)
Step type: "action" (not "command")
```

### Execution
```
Step 1: "Activate the virtual environment"
  Type: action (no command to execute)
  Result: ✅ Marked as successful (non-command steps auto-succeed)
```

### Validation Results

| Check | Result | Reason |
|-------|--------|--------|
| 1. All steps executed | ✅ PASS | 1/1 steps executed |
| 2. All steps successful | ✅ PASS | 1/1 steps succeeded |
| 3. No errors | ✅ PASS | 0 errors |
| 4. Expected files created | ✅ PASS | No files required |
| 5. Expected commands run | ❌ **FAIL** | `source .venv/bin/activate` not executed |

**Overall: ❌ FAIL**

### Why It Failed

The skill content is too abstract - it says "Activate the virtual environment" but doesn't provide the actual command. No commands were extracted, so nothing was executed. Check #5 fails because the expected command was never run.

---

## Command Extraction Logic

The framework extracts commands using these patterns:

### Pattern 1: Backticks
```markdown
Run `command --flag value`
```
Extracts: `command --flag value`

### Pattern 2: Code Blocks
````markdown
```bash
command1
command2
```
````
Extracts: `command1`, `command2`

### Pattern 3: Numbered Steps
```markdown
1) Do something with `command`
2) Do another thing with `command2`
```
Extracts: `command`, `command2`

### What's NOT Extracted
- Simple words in backticks: `activate`
- Text without command syntax: `the environment`
- Comments in code blocks: `# This is a comment`

---

## Mock Environment Simulation

The framework simulates command execution without actually running them:

### Simulated Commands

| Command Pattern | Simulated Behavior |
|----------------|-------------------|
| `orchestrate agents import` | Checks if file exists, returns success/error |
| `orchestrate env activate` | Returns "Environment activated" |
| `source .venv/bin/activate` | Sets VIRTUAL_ENV variable |
| `python` / `python3` | Returns "Python script executed" |
| `pip install` | Returns "Packages installed" |
| Other commands | Returns "Command executed: {command}" |

### File Operations

- Files can be created in setup
- Commands can check if files exist
- Missing files cause errors

---

## How to Make Skills Pass

### ❌ Bad: Abstract Instructions
```markdown
Activate the virtual environment
```
**Problem:** No command to execute

### ✅ Good: Explicit Command
```markdown
Activate the virtual environment:
```bash
source .venv/bin/activate
```
```

---

### ❌ Bad: Multiple Steps in One Sentence
```markdown
To do X: 1) Do A. 2) Run `cmd1`. 3) Run `cmd2`.
```
**Problem:** Treated as one step, only first command extracted

### ✅ Good: Separate Numbered Steps
```markdown
## Steps

1. Do A

2. Run command:
   ```bash
   cmd1
   ```

3. Run command:
   ```bash
   cmd2
   ```
```

---

### ❌ Bad: Commands Buried in Prose
```markdown
After doing X, you should run `cmd1` and then `cmd2` to finish.
```
**Problem:** Multiple commands in one step, may not all execute

### ✅ Good: One Command Per Step
```markdown
## Steps

1. Run first command:
   ```bash
   cmd1
   ```

2. Run second command:
   ```bash
   cmd2
   ```
```

---

## Summary

**The evaluation checks 5 things:**

1. ✅ Were all steps executed?
2. ✅ Did all steps succeed?
3. ✅ Were there no errors?
4. ✅ Were expected files created?
5. ✅ Were expected commands run?

**All 5 must pass for the skill to pass.**

**Most failures happen at check #5** because:
- Commands aren't extracted properly (buried in prose)
- Commands aren't provided (too abstract)
- Wrong commands are extracted (multiple in one step)

**Use the execution plan analyzer** to see what will be extracted before running tests!