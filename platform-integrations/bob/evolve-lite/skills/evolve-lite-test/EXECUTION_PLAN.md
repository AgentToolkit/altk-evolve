# Execution Plan Analyzer

## Overview

The execution plan analyzer shows exactly what the functional test framework "sees" when it analyzes a skill. This helps you understand:

- How many steps are extracted from your skill
- Which commands are found and would be executed
- Whether your skill is likely to pass functional tests
- Why a skill might fail

## Usage

### Analyze All Skills
```bash
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py
```

### Analyze Specific Skills
```bash
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py \
  .evolve/entities/atomic-skill/my-skill.md
```

### Filter by Type
```bash
# Only atomic skills
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py --type atomic-skill

# Only skill flows
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py --type skill-flow

# Only guidelines
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py --type guideline
```

### Verbose Mode
```bash
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py --verbose
```

## Understanding the Output

### Example Output

```
======================================================================
EXECUTION PLAN: activate-the-virtual-environment
======================================================================
Type: atomic-skill
Path: .evolve/entities/atomic-skill/activate-the-virtual-environment.md

──────────────────────────────────────────────────────────────────────
SKILL CONTENT:
──────────────────────────────────────────────────────────────────────
Activate the virtual environment

──────────────────────────────────────────────────────────────────────
EXTRACTED STEPS: 1
──────────────────────────────────────────────────────────────────────

📋 Step 1
   Description: Activate the virtual environment
   Commands found: 0
   └─ ⚠️  No executable commands found

──────────────────────────────────────────────────────────────────────
EXECUTION SUMMARY:
──────────────────────────────────────────────────────────────────────
Total steps: 1
Total commands: 0
Steps with commands: 0
Steps without commands: 1

──────────────────────────────────────────────────────────────────────
FUNCTIONAL TEST PREDICTION:
──────────────────────────────────────────────────────────────────────
❌ LIKELY TO FAIL: No executable commands found
   Reason: Skill content is too abstract or missing commands
```

### Sections Explained

1. **SKILL CONTENT** - The actual content of the skill (first 500 chars)

2. **EXTRACTED STEPS** - How many steps the analyzer found
   - Looks for numbered lists (1), 2), 3), etc.)
   - If no numbered steps, treats entire content as one step

3. **Step Details** - For each step:
   - 📋 Step number
   - Description (first 100 chars)
   - Commands found (count)
   - List of actual commands that would be executed

4. **EXECUTION SUMMARY** - Overall statistics:
   - Total steps found
   - Total commands found
   - Steps with vs without commands

5. **FUNCTIONAL TEST PREDICTION** - Likely test outcome:
   - ✅ **MAY PASS** - Commands found and extractable
   - ⚠️ **MAY FAIL** - Multiple commands in single step
   - ❌ **LIKELY TO FAIL** - No executable commands found

## Common Patterns

### ❌ Pattern 1: No Commands Found

**Skill Content:**
```
Activate the virtual environment
```

**Problem:** Too abstract, no actual command

**Fix:**
```
Activate the virtual environment with:
```bash
source .venv/bin/activate
```
```

---

### ⚠️ Pattern 2: Multiple Commands in One Step

**Skill Content:**
```
To do X: 1) Do A. 2) Do B with `cmd1`. 3) Do C with `cmd2`.
```

**Problem:** All in one sentence, only first command may execute

**Fix:**
```markdown
## Steps

1. Do A

2. Do B
   ```bash
   cmd1
   ```

3. Do C
   ```bash
   cmd2
   ```
```

---

### ✅ Pattern 3: Clear Extractable Commands

**Skill Content:**
```markdown
## Steps

1. Activate environment
   ```bash
   source .venv/bin/activate
   ```

2. Run command
   ```bash
   orchestrate agents import --file agent.yaml
   ```
```

**Result:** Each step has clear, extractable commands

---

## Real-World Examples from Analysis

### Example 1: Virtual Environment Activation

**Current State:**
```
Activate the virtual environment
```

**Execution Plan:**
- Steps: 1
- Commands: 0
- Prediction: ❌ LIKELY TO FAIL

**Why:** No executable command provided

**Fix:**
```markdown
Activate the virtual environment:
```bash
source .venv/bin/activate
```
```

---

### Example 2: Orchestrate Agent Creation

**Current State:**
```
To create and upload a Watson Orchestrate agent: 1) Create a YAML file... 
2) Activate the virtual environment. 3) Ensure the orchestrate environment 
is authenticated with `orchestrate env activate`. 4) Import the agent with 
`orchestrate agents import --file <agent.yaml>`.
```

**Execution Plan:**
- Steps: 1 (should be 4!)
- Commands: 2 (`orchestrate env activate`, `orchestrate agents import`)
- Prediction: ⚠️ MAY FAIL (multiple commands in single step)

**Why:** All 4 steps compressed into one sentence

**Fix:**
```markdown
## Steps

1. Create YAML file with required fields

2. Activate virtual environment
   ```bash
   source .venv/bin/activate
   ```

3. Authenticate with Orchestrate
   ```bash
   orchestrate env activate
   ```

4. Import the agent
   ```bash
   orchestrate agents import --file agent.yaml
   ```
```

---

### Example 3: Well-Structured Skill

**Current State:**
```markdown
When using Watson Orchestrate CLI commands, always activate the virtual 
environment first with `source .venv/bin/activate`.
```

**Execution Plan:**
- Steps: 1
- Commands: 1 (`source .venv/bin/activate`)
- Prediction: ✅ MAY PASS

**Why:** Clear command in backticks, easy to extract

---

## Command Extraction Rules

The analyzer extracts commands from:

1. **Code blocks:**
   ````markdown
   ```bash
   command here
   ```
   ````

2. **Backticks with spaces or special chars:**
   ```markdown
   Run `command --flag value`
   ```

3. **Not extracted:**
   - Simple words in backticks: `activate`
   - Text without command syntax: `the environment`

## Using Results to Improve Skills

### Step 1: Run the Analyzer
```bash
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py
```

### Step 2: Review Predictions

Look for:
- ❌ Skills with no commands
- ⚠️ Skills with multiple commands in one step
- Skills with only 1 step when there should be more

### Step 3: Fix Problem Skills

For each problematic skill:
1. Add clear numbered steps
2. Put commands in code blocks
3. One command per step
4. Make steps actionable

### Step 4: Verify Improvements
```bash
# Re-run analyzer on fixed skill
python3 .bob/skills/evolve-lite-test/scripts/show_execution_plan.py \
  .evolve/entities/atomic-skill/my-fixed-skill.md
```

### Step 5: Run Functional Tests
```bash
# Confirm the skill now passes
python3 .bob/skills/evolve-lite-test/scripts/run_skill_functional_tests.py
```

---

## Integration with Testing Workflow

```
1. Write/update skill
   ↓
2. Run execution plan analyzer
   ↓
3. Review predictions
   ↓
4. Fix any issues
   ↓
5. Run functional tests
   ↓
6. Confirm pass
```

---

## Summary

The execution plan analyzer helps you:

- **Visualize** what the test framework sees
- **Predict** whether skills will pass functional tests
- **Identify** structural problems before testing
- **Fix** issues proactively
- **Improve** skill quality systematically

Use it as a **pre-flight check** before running functional tests!