# Why Functional Tests Fail - Visual Explanation

## Problem 1: Orchestrate Agent Skill

### Current Skill Content (Line 10)
```
To create and upload a Watson Orchestrate agent: 1) Create a YAML file with spec_version, name, description, instructions, model, parameters, and tools fields. 2) Activate the virtual environment. 3) Ensure the orchestrate environment is authenticated with `orchestrate env activate`. 4) Import the agent with `orchestrate agents import --file <agent.yaml>`.
```

### What the Test Sees
The step extraction algorithm sees this as **ONE LONG SENTENCE** with:
- Multiple numbered items (1, 2, 3, 4) embedded in prose
- Two commands in backticks: `orchestrate env activate` and `orchestrate agents import --file <agent.yaml>`
- But it only extracts the FIRST command it finds

### What Gets Executed
```
Step 1: "Create a YAML file... [entire sentence]"
Command extracted: orchestrate env activate
Command executed: ✅ orchestrate env activate
```

### What SHOULD Be Executed
```
Expected command: orchestrate agents import
Actual command run: orchestrate env activate
Result: ❌ FAIL - Wrong command executed!
```

### Why It Fails
The test expects `orchestrate agents import` to be executed, but the skill format makes it impossible to extract properly because:
1. All 4 steps are in one sentence
2. Commands are buried in prose
3. Step extraction finds the first command, not the important one

---

## Problem 2: Virtual Environment Skill

### Current Skill Content (Line 9)
```
Activate the virtual environment
```

### What the Test Sees
- A single abstract instruction
- No commands in backticks
- No code blocks
- No executable content

### What Gets Executed
```
Step 1: "Activate the virtual environment"
Type: action (not command)
Commands extracted: 0
Commands executed: 0
```

### What SHOULD Be Executed
```
Expected command: source .venv/bin/activate
Actual commands run: (none)
Result: ❌ FAIL - No commands executed!
```

### Why It Fails
The test expects `source .venv/bin/activate` to be executed, but the skill doesn't contain any executable command - it's just an abstract instruction.

---

## The Solution: Proper Skill Structure

### ✅ Good Skill Format

```markdown
## Steps

1. Create the YAML file with required fields
   ```bash
   cat > agent.yaml << EOF
   spec_version: v1
   name: my-agent
   ...
   EOF
   ```

2. Activate the virtual environment
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

### Why This Works

1. **Clear numbered steps** - Each step is separate
2. **Commands in code blocks** - Easy to extract
3. **One command per step** - No ambiguity
4. **Executable** - Can be run directly

---

## Visual Comparison

### ❌ Current Format (Fails)
```
To do X: 1) Do A. 2) Do B. 3) Run `cmd1`. 4) Run `cmd2`.
```
**Problem:** Everything in one sentence, hard to parse

### ✅ Proper Format (Passes)
```markdown
## Steps
1. Do A
2. Do B
3. Run command:
   ```bash
   cmd1
   ```
4. Run command:
   ```bash
   cmd2
   ```
```
**Solution:** Clear structure, easy to parse

---

## Real-World Impact

### If You Try to Use These Skills

**Orchestrate Agent Skill:**
- You'd get guidance to run `orchestrate env activate`
- But the critical command `orchestrate agents import` is buried
- You might miss it or not know when to run it

**Virtual Environment Skill:**
- You'd be told "Activate the virtual environment"
- But HOW? What command?
- You'd have to already know the answer

---

## The Tests Are Working!

The functional tests are **correctly identifying** that these skills:
- ❌ Can't be executed automatically
- ❌ Don't have clear, extractable steps
- ❌ Bury important commands in prose
- ❌ Are too abstract to be actionable

This is **exactly what testing should reveal** - skills that look okay but don't work in practice!

---

## Next Steps

To make these skills pass:

1. **Restructure with clear numbered steps**
2. **Put commands in code blocks**
3. **Make each step actionable**
4. **Test that commands can be extracted**

The functional tests will then pass, confirming the skills are executable.