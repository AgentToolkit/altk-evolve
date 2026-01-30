# Save Skill Documentation

## Overview

The **save** skill is a powerful tool that captures successful workflows from your current session and transforms them into reusable skills. It analyzes your conversation history, identifies patterns, and generates comprehensive documentation along with helper scripts.

## Location

The save skill is part of the Kaizen plugin:
```
/plugins/kaizen/skills/save/SKILL.md
```

## What It Does

When you invoke the `save` skill after completing a successful task, it:

1. **Analyzes your session** - Reviews user requests, reasoning steps, tool calls, and responses
2. **Identifies patterns** - Extracts the workflow sequence and decision points
3. **Parameterizes values** - Converts session-specific values into reusable parameters
4. **Generates documentation** - Creates a comprehensive SKILL.md file
5. **Creates helper scripts** - Generates Python scripts for programmatic operations (when applicable)
6. **Saves everything** - Stores the skill in `~/.claude/skills/{skill-name}/`

## When to Use

Use the save skill when you:
- Complete a multi-step task successfully
- Discover a useful workflow pattern
- Want to standardize a process for future use
- Solve a problem that might recur
- Work through a complex workflow that involves data processing, file operations, or API calls

## How to Use

### Basic Usage

After completing a successful task:

```
User: "save"
```

The skill will:
1. Analyze your current session
2. Ask you for a skill name
3. Generate SKILL.md and any helper scripts
4. Save to `~/.claude/skills/{skill-name}/`
5. Provide a detailed summary

### Example Session

```
User: "Read the config.json file and parse it"
Assistant: [Successfully reads and parses the file]
User: "Great! save"
Assistant: "What would you like to name this skill?"
User: "read-and-parse-config"
Assistant: [Generates skill and scripts]
```

## Generated Output

### Directory Structure

```
~/.claude/skills/{skill-name}/
├── SKILL.md              # Comprehensive documentation
└── scripts/              # Helper scripts (if applicable)
    ├── parse_data.py
    └── validate_data.py
```

### SKILL.md Contents

The generated SKILL.md includes:
- **Overview**: What the skill does and when to use it
- **Parameters**: Required inputs with descriptions
- **Workflow**: Step-by-step instructions
- **Helper Scripts**: Documentation for any generated scripts
- **Error Handling**: Common errors and solutions
- **Examples**: Real-world usage examples
- **Notes**: Additional tips and context

### Helper Scripts

Scripts are automatically generated when your workflow includes:
- Data transformation or parsing (JSON, CSV, XML)
- File operations (reading, writing, searching)
- API calls or HTTP requests
- Complex calculations or data analysis
- Repetitive operations that could be automated

## Parameterization

The save skill uses **conservative parameterization**, meaning it only parameterizes obvious session-specific values:

**Parameterized**:
- File paths: `/home/user/project/file.txt` → `{project_dir}/{filename}`
- Specific names: `myapp` → `{app_name}`
- User data: `john@example.com` → `{email}`

**Kept Unchanged**:
- Tool names: `read_file`, `execute_command`
- General patterns and logic
- Error handling approaches
- Workflow structure

## Examples

### Example 1: File Reading Workflow

**Session**:
```
User: "Read states.txt using the filesystem MCP tool"
Assistant: [Handles permission errors, searches for file, reads successfully]
User: "save"
```

**Generated Skill**: `read-file-with-permission-check`

**Files Created**:
```
~/.claude/skills/read-file-with-permission-check/
├── SKILL.md
└── scripts/
    └── search_and_read.py
```

### Example 2: API Integration Workflow

**Session**:
```
User: "Fetch user data from the API and format it"
Assistant: [Makes API call, processes response, formats output]
User: "save"
```

**Generated Skill**: `fetch-and-format-user-data`

**Files Created**:
```
~/.claude/skills/fetch-and-format-user-data/
├── SKILL.md
└── scripts/
    ├── api_client.py
    └── format_data.py
```

### Example 3: Simple Tool Call (No Scripts)

**Session**:
```
User: "List all Python files in the project"
Assistant: [Uses glob tool to find *.py files]
User: "save"
```

**Generated Skill**: `list-python-files`

**Files Created**:
```
~/.claude/skills/list-python-files/
└── SKILL.md
```

## Skill Naming Guidelines

When prompted for a skill name, follow these guidelines:

- **Use lowercase letters**: `my-skill` not `My-Skill`
- **Separate words with hyphens**: `read-and-parse` not `read_and_parse`
- **Be descriptive**: `deploy-to-staging` not `deploy`
- **Keep it concise**: `analyze-logs` not `analyze-application-logs-for-errors`

**Good Examples**:
- `read-file-with-permissions`
- `deploy-to-staging`
- `analyze-logs`
- `fetch-user-data`

**Bad Examples**:
- `My Skill!` (spaces and special characters)
- `skill` (too generic)
- `read_file` (underscores instead of hyphens)

## Handling Conflicts

If a skill with the chosen name already exists, you'll be prompted to:
- **Overwrite** the existing skill
- **Choose a different name**
- **Cancel** the operation

## Tips for Better Skills

1. **Complete the task first**: Ensure your workflow is successful before saving
2. **Clear session**: The clearer your workflow, the better the generated skill
3. **Descriptive names**: Choose names that clearly indicate what the skill does
4. **Test the skill**: After saving, test it in a new session to verify it works
5. **Refine manually**: Edit the generated SKILL.md and scripts to add more context or examples

## Advanced Usage

### Manual Editing

After generation, you can manually edit:
- **SKILL.md**: Add more examples, refine descriptions, update parameters
- **Scripts**: Add error handling, optimize performance, add features

### Skill Composition

Generated skills can reference other skills:

```markdown
## Workflow

### Step 1: Fetch Data
Use the `fetch-user-data` skill to retrieve user information.

### Step 2: Process Data
Use the `parse-json-data` skill to parse the response.
```

### Version Control

Consider adding your `~/.claude/skills/` directory to version control:

```bash
cd ~/.claude/skills
git init
git add .
git commit -m "Initial skills collection"
```

## Troubleshooting

### Session Too Short

**Problem**: "Session has fewer than 3 meaningful exchanges"

**Solution**: Complete more of the task before invoking the save skill

### No Clear Workflow

**Problem**: "Conversation doesn't show a clear workflow pattern"

**Solution**: Describe the key steps you want to capture when prompted

### Invalid Skill Name

**Problem**: "Skill name contains invalid characters"

**Solution**: Use lowercase letters and hyphens only (e.g., `my-skill`)

### Script Generation Errors

**Problem**: Script generation fails

**Solution**: The SKILL.md will still be saved. You can add scripts manually later.

## Technical Details

### Session Context

The save skill accesses the current session context, which includes:
- User utterances
- Agent reasoning (thinking tags)
- Tool calls with arguments
- Tool responses
- Agent responses

No external fetching or Phoenix integration is required - the context is automatically available when the skill is invoked.

### Storage Location

Skills are saved to `~/.claude/skills/` in your home directory, making them available across all projects.

### Script Templates

Generated scripts follow a standard template with:
- Proper argument parsing
- Error handling
- JSON output (when appropriate)
- Usage documentation
- Executable permissions

## Support

For issues or questions about the save skill:
1. Review the generated SKILL.md for specific workflow questions
2. Manually edit generated skills to customize them for your needs
