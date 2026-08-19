# Critical Issue: Extraction Logic Mismatch

## The Problem

The **execution plan analyzer** and the **functional test** use **different step extraction logic**, causing them to show different results!

---

## Execution Plan Analyzer Logic

**File:** `show_execution_plan.py` (line 28)

```python
numbered_pattern = r'^\s*(\d+)[.)]\s+(.+?)(?=^\s*\d+[.)]|\Z)'
matches = re.finditer(numbered_pattern, content, re.MULTILINE | re.DOTALL)
```

**What it does:**
- Captures from one numbered item to the next (or end of content)
- Uses `re.DOTALL` - captures across multiple lines
- Pattern: `(.+?)` - captures EVERYTHING until next number

**Example with Orchestrate Agent Skill:**
```
Content: "To create... 1) Create a YAML file... 2) Activate... 3) Run `cmd1`. 4) Run `cmd2`."

Extraction:
  Step 1: "Create a YAML file... 2) Activate... 3) Run `cmd1`. 4) Run `cmd2`."
  Commands found: cmd1, cmd2 (extracts ALL commands from entire content)
```

---

## Functional Test Logic

**File:** `run_skill_functional_tests.py` (line 163)

```python
numbered_pattern = r'(\d+)[.)]\s+([^\n]+)'
matches = re.finditer(numbered_pattern, content)
```

**What it does:**
- Captures only up to the newline
- Pattern: `([^\n]+)` - stops at newline character
- Only extracts FIRST command in backticks per line

**Example with Orchestrate Agent Skill:**
```
Content: "To create... 1) Create a YAML file... 2) Activate... 3) Run `cmd1`. 4) Run `cmd2`."

Extraction:
  Step 1: "Create a YAML file..."
  Step 2: "Activate..."
  Step 3: "Run `cmd1`."
  Step 4: "Run `cmd2`."
  
But since it's all on ONE line, it only matches:
  Step 1: "Create a YAML file... 2) Activate... 3) Run `cmd1`. 4) Run `cmd2`."
  Command: cmd1 (only FIRST command in backticks)
```

---

## Why This Causes Confusion

### Execution Plan Shows:
```
Steps: 1
Commands: 2 (orchestrate env activate, orchestrate agents import)
Prediction: ⚠️ MAY FAIL
```

### Functional Test Actually Does:
```
Steps: 1
Commands extracted: 1 (orchestrate env activate - FIRST command only)
Commands executed: 1 (orchestrate env activate)
Result: ❌ FAIL - orchestrate agents import never executed
```

---

## The Real Issue

For the Orchestrate Agent skill:
```
To create and upload a Watson Orchestrate agent: 1) Create a YAML file with spec_version, name, description, instructions, model, parameters, and tools fields. 2) Activate the virtual environment. 3) Ensure the orchestrate environment is authenticated with `orchestrate env activate`. 4) Import the agent with `orchestrate agents import --file <agent.yaml>`.
```

**This is ALL ONE LINE** - no newlines between numbered items!

### Execution Plan Analyzer:
- Sees: 1 step (entire line)
- Extracts: Both commands (`orchestrate env activate`, `orchestrate agents import`)
- Shows: 2 commands found

### Functional Test:
- Sees: 1 step (entire line, stops at newline which never comes)
- Extracts: Only FIRST command (`orchestrate env activate`)
- Executes: Only that one command
- Fails: Because `orchestrate agents import` never runs

---

## Why Virtual Environment Skill Also Fails

**Skill content:**
```
Activate the virtual environment
```

### Both Scripts:
- See: 1 step
- Extract: 0 commands (no backticks, no code blocks)
- Result: No commands to execute

The functional test marks it as "action" type and auto-succeeds, but check #5 fails because the expected command `source .venv/bin/activate` was never executed.

---

## The Fix

### Option 1: Fix the Functional Test Extraction

Make it match the execution plan analyzer:

```python
# Change from:
numbered_pattern = r'(\d+)[.)]\s+([^\n]+)'

# To:
numbered_pattern = r'^\s*(\d+)[.)]\s+(.+?)(?=^\s*\d+[.)]|\Z)'
matches = re.finditer(numbered_pattern, content, re.MULTILINE | re.DOTALL)
```

**Problem:** This still won't help because the skill content is poorly formatted (all on one line).

### Option 2: Fix the Skills (RECOMMENDED)

Restructure skills with proper formatting:

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

**This works with BOTH extraction methods!**

---

## Why Option 2 is Better

1. **Clear structure** - Each step is separate
2. **Explicit commands** - In code blocks, easy to extract
3. **Works with both extractors** - No ambiguity
4. **Human readable** - Easy to understand and follow
5. **Maintainable** - Easy to update individual steps

---

## Action Items

1. **Update functional test extraction** to match execution plan analyzer (for consistency)
2. **Document skill formatting standards** based on what works
3. **Refactor existing skills** to use proper structure
4. **Add validation** to catch poorly formatted skills before they're saved

---

## Lesson Learned

**The tests aren't broken - they're revealing that:**
1. Skills are poorly formatted (all on one line)
2. Extraction logic is inconsistent between tools
3. We need clear skill formatting standards

The 0% pass rate is actually **correct** - these skills genuinely don't work as written!