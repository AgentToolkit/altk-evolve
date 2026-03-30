---
name: save
description: Captures the current session's successful workflow and saves it as a reusable skill with SKILL.md and helper scripts
---

# Save Session as Skill

## Overview

This skill analyzes your current successful session and generates a new reusable skill with:
- **SKILL.md**: Comprehensive documentation with workflow steps, parameters, and examples
- **Helper scripts**: Python scripts for any programmatic operations identified in the workflow

It extracts the workflow pattern from your conversation history (user requests, reasoning steps, tool calls, and responses) and creates parameterized files that can be invoked in future sessions.

Use this skill when you've completed a task successfully and want to save the workflow for future reuse.

## When to Use

- After completing a multi-step task successfully
- When you've discovered a useful workflow pattern
- When you want to standardize a process for future use
- After solving a problem that might recur
- When the workflow involves programmatic operations that could benefit from helper scripts

## Workflow

### Step 1: Review Current Session

Analyze the conversation history available in the current context, which includes:

- **User messages**: All requests and questions from the user
- **Assistant reasoning**: Thinking tags and decision-making process
- **Tool calls**: All tools invoked with their arguments
- **Tool responses**: Results and outcomes from each tool
- **Final outcome**: The successful result achieved

**Action**: Review the entire conversation from start to current point

### Step 2: Identify the Workflow Pattern

Extract the high-level workflow by:

1. **Identifying the goal**: What was the user trying to accomplish?
2. **Grouping related actions**: Which tool calls belong together as logical steps?
3. **Recognizing decision points**: Where did the workflow branch based on conditions?
4. **Noting error handling**: How were errors or edge cases handled?
5. **Extracting the sequence**: What is the step-by-step process?

**Example Pattern Recognition**:
```
User Goal: "Read a file and display its contents"

Workflow Pattern:
1. Attempt to read file at expected location
2. If access denied → check allowed directories
3. Search for file in allowed directories
4. Read file from correct location
5. Format and present results
```

### Step 3: Identify Parameterizable Values

Apply **conservative parameterization** - only parameterize obvious session-specific values:

**Parameterize**:
- Absolute file paths → `{file_path}` or `{directory}`
- Specific file names → `{filename}`
- User-specific data → `{data_value}`
- Project-specific names → `{project_name}`
- Workspace directories → `{workspace_dir}`

**Keep Unchanged**:
- Tool names (e.g., `read_file`, `execute_command`)
- General patterns and logic
- Error handling approaches
- Workflow structure

**Example**:
```
Original: "Read /home/user/projects/myapp/config.json"
Parameterized: "Read {project_dir}/{config_file}"
```

### Step 4: Identify Script Opportunities

Analyze the workflow to determine if helper scripts would be beneficial:

**Generate scripts when the workflow includes**:
- Data transformation or parsing (JSON, CSV, XML processing)
- File operations (reading, writing, searching, filtering)
- API calls or HTTP requests
- Complex calculations or data analysis
- Repetitive operations that could be automated
- Integration with external tools or services

**Script Types to Consider**:
- **Data processors**: Parse, transform, or validate data
- **File handlers**: Read, write, or manipulate files
- **API clients**: Interact with external services
- **Validators**: Check inputs or outputs
- **Formatters**: Convert data between formats

**Example**:
```
Workflow includes: Reading JSON file, extracting specific fields, formatting output
→ Generate: parse_and_format.py script
```

### Step 5: Generate Skill Document

Create a new SKILL.md file with the following structure:

```markdown
---
name: {skill-name}
description: {one-line description of what this skill does}
---

# {Skill Title}

## Overview

{Brief description of the skill's purpose and when to use it}

## Parameters

{List parameters the user needs to provide}

- **{param_name}**: {description and example}

## Workflow

### Step 1: {Step Name}

{What this step does}

**Action**: {Tool or approach to use}

**Example**:
```
{Example tool call or command}
```

{If helper script exists, reference it}
**Helper Script**: Use `scripts/{script_name}.py` for this operation

{Repeat for each step}

## Helper Scripts

{If scripts were generated, document them}

### {script_name}.py

**Purpose**: {What the script does}

**Usage**:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/{skill-name}/scripts/{script_name}.py [arguments]
```

**Parameters**:
- `{param}`: {description}

**Example**:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/{skill-name}/scripts/parse_data.py input.json
```

## Error Handling

{Common errors and how to handle them}

## Examples

### Example 1: {Use Case}

**Input**:
- {param}: {value}

**Expected Output**:
{What the user should see}

## Notes

{Additional tips or context}
```

### Step 6: Generate Helper Scripts

For each identified script opportunity, create a Python script with:

**Script Template**:
```python
#!/usr/bin/env python3
"""
{Script description}

Usage:
    python3 {script_name}.py [arguments]

Arguments:
    {arg1}: {description}
    {arg2}: {description}
"""

import sys
import json
import argparse
from pathlib import Path


def main():
    """Main function implementing the script logic."""
    parser = argparse.ArgumentParser(description="{Script description}")
    parser.add_argument("{arg1}", help="{description}")
    parser.add_argument("{arg2}", help="{description}", nargs="?")
    
    args = parser.parse_args()
    
    # Implementation based on workflow pattern
    try:
        # Core logic here
        result = process_data(args.{arg1})
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def process_data(input_data):
    """Process the input data according to the workflow pattern."""
    # Implementation extracted from session workflow
    pass


if __name__ == "__main__":
    main()
```

**Script Guidelines**:
- Include proper error handling
- Accept parameters via command-line arguments
- Output results in a structured format (JSON when appropriate)
- Include usage documentation in docstring
- Make scripts executable (`chmod +x`)

### Step 7: Prompt for Skill Name

Ask the user: **"What would you like to name this skill?"**

**Naming Guidelines**:
- Use lowercase letters
- Separate words with hyphens (kebab-case)
- Be descriptive but concise
- Examples: `read-file-with-permissions`, `deploy-to-staging`, `analyze-logs`

**Suggest a name** based on the workflow if the user is unsure:
- Extract key actions and objects from the workflow
- Combine into a descriptive name
- Example: "Read file → Check permissions → Search" → `read-file-with-permission-check`

### Step 8: Check for Existing Skill

Before saving, check if a skill with this name already exists:

**Action**: Check if `~/.claude/skills/{skill-name}/SKILL.md` exists

**If exists**:
- Inform the user
- Ask: "A skill with this name already exists. Would you like to:"
  - Overwrite the existing skill
  - Choose a different name
  - Cancel

### Step 9: Save the Skill

**Action**: Create the skill directory structure and save all files

1. Create directory: `~/.claude/skills/{skill-name}/`
2. Write SKILL.md to: `~/.claude/skills/{skill-name}/SKILL.md`
3. If scripts were generated:
   - Create directory: `~/.claude/skills/{skill-name}/scripts/`
   - Write each script to: `~/.claude/skills/{skill-name}/scripts/{script_name}.py`
   - Make scripts executable: `chmod +x ~/.claude/skills/{skill-name}/scripts/*.py`
4. Ensure proper permissions (readable by user)

**Directory Structure**:
```
~/.claude/skills/{skill-name}/
├── SKILL.md
└── scripts/           (if applicable)
    ├── script1.py
    └── script2.py
```

**Note**: The skill is saved to the user's home directory (`~/.claude/skills/`) making it available across all projects.

### Step 10: Provide Summary

Present a clear summary to the user:

```
✅ Skill saved successfully!

**Skill Name**: {skill-name}
**Location**: ~/.claude/skills/{skill-name}/

**Files Created**:
- SKILL.md (workflow documentation)
{if scripts}
- scripts/{script1}.py (helper script for {purpose})
- scripts/{script2}.py (helper script for {purpose})
{endif}

**Summary**: {Brief description of what the skill does}

**Workflow Captured**:
1. {Step 1 summary}
2. {Step 2 summary}
3. {Step 3 summary}
...

**Parameters**:
- **{param1}**: {description}
- **{param2}**: {description}

**Helper Scripts**:
{if scripts}
- **{script1}.py**: {what it does}
- **{script2}.py**: {what it does}
{endif}

**To use this skill**: Simply reference it by name in future sessions: "{skill-name}"
```

## Error Handling

**Session Too Short**:
- If the session has fewer than 3 meaningful exchanges, inform the user
- Suggest completing more of the task before saving as a skill

**No Clear Workflow**:
- If the conversation doesn't show a clear workflow pattern, ask the user to clarify
- Request: "Could you describe the key steps you want to capture?"

**Skill Name Conflicts**:
- If the name already exists, provide options (overwrite, rename, cancel)
- Never silently overwrite without user confirmation

**Invalid Skill Name**:
- If the name contains invalid characters (spaces, special chars), suggest corrections
- Example: "My Skill!" → "my-skill"

**Script Generation Errors**:
- If script generation fails, save the SKILL.md anyway
- Inform user they can add scripts manually later
- Provide guidance on what the script should do

## Examples

### Example 1: Saving a File Reading Workflow (with script)

**Session Context**:
```
User: "Read the states.txt file and parse it into a JSON array"
Assistant: [Reads file, parses lines, converts to JSON, outputs result]
User: "Great! Save this as a skill"
```

**Generated Skill Name**: `read-and-parse-file`

**Parameters Identified**:
- `filename`: The file to read
- `output_format`: Format for output (json, csv, etc.)

**Workflow Captured**:
1. Read file from workspace
2. Parse file contents line by line
3. Convert to specified format
4. Output formatted result

**Scripts Generated**:
- `parse_file.py`: Reads a file and converts it to JSON format

**Files Created**:
```
~/.claude/skills/read-and-parse-file/
├── SKILL.md
└── scripts/
    └── parse_file.py
```

### Example 2: Saving a Deployment Workflow (with multiple scripts)

**Session Context**:
```
User: "Deploy the app to staging"
Assistant: [Runs tests, builds app, uploads to server, restarts service]
User: "Perfect! Save this workflow"
```

**Generated Skill Name**: `deploy-to-staging`

**Parameters Identified**:
- `app_name`: Name of the application
- `server_address`: Staging server address

**Workflow Captured**:
1. Run test suite
2. Build application
3. Upload to staging server
4. Restart service
5. Verify deployment

**Scripts Generated**:
- `run_tests.py`: Execute test suite and report results
- `deploy.py`: Handle upload and service restart

**Files Created**:
```
~/.claude/skills/deploy-to-staging/
├── SKILL.md
└── scripts/
    ├── run_tests.py
    └── deploy.py
```

### Example 3: Simple Workflow (no scripts needed)

**Session Context**:
```
User: "List all Python files in the project"
Assistant: [Uses glob tool to find *.py files, displays results]
User: "Save this"
```

**Generated Skill Name**: `list-python-files`

**Workflow Captured**:
1. Use glob tool with pattern "**/*.py"
2. Format and display results

**Scripts Generated**: None (simple tool call, no script needed)

**Files Created**:
```
~/.claude/skills/list-python-files/
└── SKILL.md
```

## Notes

- **Conservative Parameterization**: Only obvious session-specific values are parameterized. You can manually edit the generated skill later for more customization.
- **Cross-Project Availability**: Skills are saved to `~/.claude/skills/` making them available in all your projects.
- **Manual Editing**: After generation, you can manually edit the SKILL.md file and scripts to refine the workflow, add more examples, or adjust parameters.
- **Script Reusability**: Generated scripts can be used standalone or called from other scripts.
- **Skill Composition**: Generated skills can reference other skills, creating powerful workflow chains.
- **Version Control**: Consider adding your `~/.claude/skills/` directory to version control to track skill evolution.

## Tips for Better Skills

1. **Complete the task first**: Make sure your workflow is successful before saving it as a skill
2. **Clear session**: The clearer your session workflow, the better the generated skill and scripts
3. **Descriptive names**: Choose skill names that clearly indicate what they do
4. **Test scripts**: After generation, test the helper scripts to ensure they work correctly
5. **Add context**: After generation, consider adding more examples or notes to the skill
6. **Refine scripts**: Review generated scripts and add error handling or features as needed
7. **Document parameters**: Ensure all script parameters are well-documented in both SKILL.md and script docstrings
