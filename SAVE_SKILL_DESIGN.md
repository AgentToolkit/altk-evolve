# Save Session as Skill - Design Document

## Overview

This skill enables users to capture successful workflows from their current session and save them as reusable skills. It analyzes the session trajectory (user requests, tool calls, reasoning steps) and generates a new skill document that can be invoked in future sessions.

## Key Concepts

- **Session Trajectory**: The complete conversation history including user messages, assistant reasoning, tool calls, and tool responses
- **Workflow Pattern**: The sequence of steps and tools used to accomplish a task
- **Parameterization**: Identifying session-specific values that should become parameters in the new skill
- **Skill Template**: The generated SKILL.md file that captures the workflow

## Architecture Flow

1. User completes a successful task/workflow in their session
2. User invokes the "save-session-as-skill" skill
3. Skill analyzes the current session trajectory to extract:
   - User's original request/goal
   - Sequence of reasoning steps
   - Tool calls made (with arguments)
   - Tool responses and outcomes
   - Final result/output
4. Skill identifies parameterizable values (file paths, names, specific data)
5. Skill generates a new SKILL.md document with:
   - Clear description of what the skill does
   - Step-by-step workflow instructions
   - Parameter placeholders for customizable values
   - Examples of usage
6. Skill prompts user for the new skill name
7. Skill saves the new skill to the appropriate directory
8. Skill provides summary of what was saved and where

## Workflow Design

### Step 1: Analyze Current Session

**Input**: Current session trajectory (available via environment or Phoenix)

**Process**:
- Extract all user messages to understand the task goal
- Extract all assistant reasoning/thinking to understand the approach
- Extract all tool calls to understand the actions taken
- Extract all tool responses to understand what worked
- Identify the final successful outcome

**Output**: Structured analysis of the session

### Step 2: Identify Workflow Pattern

**Process**:
- Group related tool calls into logical steps
- Identify decision points and conditional logic
- Recognize error handling and recovery patterns
- Extract the high-level workflow sequence

**Example Pattern Recognition**:
```
User Request: "Read states.txt and list my teammates' locations"

Workflow Pattern Identified:
1. Attempt to read file at expected location
2. If access denied, check allowed directories
3. Search for file in allowed directories
4. Read file from correct location
5. Parse and format the results
```

### Step 3: Parameterize Session-Specific Values

**Process**:
- Identify values that are specific to this session:
  - File paths (e.g., `/home/user/...` → `{file_path}`)
  - File names (e.g., `states.txt` → `{filename}`)
  - Specific data values (e.g., `texas` → `{state_name}`)
  - Directory paths (e.g., `cuga_workspace` → `{workspace_dir}`)
  
**Parameterization Rules**:
- Absolute paths → relative or parameterized
- User-specific names → generic placeholders
- Specific data → parameter with description
- Keep tool names and general patterns unchanged

**Example**:
```
Original: "Read /home/user/workspace/my-agent/cuga_workspace/states.txt"
Parameterized: "Read {workspace_dir}/{filename}"
```

### Step 4: Generate Skill Document

**Template Structure**:
```markdown
---
name: {skill-name}
description: {one-line description of what this skill does}
---

# {Skill Title}

## Overview

{Brief description of the skill's purpose and when to use it}

## Parameters

{List of parameters the user needs to provide}

- **{param_name}**: {description of parameter}
- **{param_name}**: {description of parameter}

## Workflow

### Step 1: {Step Name}

{Description of what this step does}

**Action**: {Tool or approach to use}

**Example**:
```
{Example command or tool call}
```

### Step 2: {Step Name}

{Continue for each step...}

## Error Handling

{Common errors and how to handle them}

## Examples

### Example 1: {Use Case}

**Input**:
- {param}: {value}
- {param}: {value}

**Expected Output**:
{What the user should see}

## Notes

{Any additional context or tips}
```

### Step 5: Prompt User for Skill Name

**Process**:
- Ask user: "What would you like to name this skill?"
- Validate name (lowercase, hyphens, no spaces)
- Suggest a name based on the workflow if user is unsure

**Example Suggestions**:
- "read-file-from-workspace"
- "search-and-read-file"
- "list-teammate-locations"

### Step 6: Generate Helper Scripts (New)

**When to Generate Scripts**:
Analyze the workflow to determine if helper scripts would be beneficial:

- Data transformation or parsing (JSON, CSV, XML processing)
- File operations (reading, writing, searching, filtering)
- API calls or HTTP requests
- Complex calculations or data analysis
- Repetitive operations that could be automated
- Integration with external tools or services

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

### Step 7: Save Skill

**Process**:
- Create skill directory: `~/.claude/skills/{skill-name}/`
- Save SKILL.md to: `~/.claude/skills/{skill-name}/SKILL.md`
- If scripts were generated:
  - Create directory: `~/.claude/skills/{skill-name}/scripts/`
  - Write each script to: `~/.claude/skills/{skill-name}/scripts/{script_name}.py`
  - Make scripts executable: `chmod +x ~/.claude/skills/{skill-name}/scripts/*.py`

**Directory Structure**:
```
~/.claude/skills/{skill-name}/
├── SKILL.md
└── scripts/           (if applicable)
    ├── script1.py
    └── script2.py
```

**Validation**:
- Check if skill name already exists
- Prompt user to overwrite or choose different name

### Step 8: Provide Summary

**Output to User**:
```
✅ Skill saved successfully!

Skill Name: {skill-name}
Location: /home/user/workspace/kaizen/skills/{skill-name}/SKILL.md

Summary:
{Brief description of what the skill does}

The skill captures the following workflow:
1. {Step 1 summary}
2. {Step 2 summary}
3. {Step 3 summary}

Parameters:
- {param1}: {description}
- {param2}: {description}

To use this skill in the future, simply reference it by name: "{skill-name}"
```

## Implementation Considerations

### Accessing Session Trajectory

**Session Context is Already Available**

When the skill is invoked, the session history is already available in the context. The skill simply needs to parse this context, which includes:

- **User utterances**: All messages from the user
- **Agent reasoning steps**: Thinking tags and internal reasoning
- **Tool calls**: All tool invocations with their arguments
- **Agent responses**: All assistant messages and outputs

**No External Fetching Required**:
- No need to call Phoenix or extract_trajectories.py
- No need to check environment variables
- Context is provided automatically when skill is invoked

**Implementation Approach**:
The skill will include instructions to:
1. Review the conversation history in the current context
2. Extract relevant patterns from user messages, tool calls, and reasoning
3. Identify the workflow that led to success
4. Generate the parameterized skill document

### Skill Storage Location

**Primary Location**: `/skills/{skill-name}/SKILL.md`

**Alternative Locations**:
- User's project-specific skills: `.claude/skills/{skill-name}/SKILL.md`
- Plugin skills: `/plugins/kaizen/skills/{skill-name}/SKILL.md`

### Parameterization Strategy

**Conservative Approach** (Recommended):
- Only parameterize obvious session-specific values
- Keep most of the workflow concrete
- User can manually edit the skill later for more customization

**Aggressive Approach**:
- Parameterize many values
- Create more flexible but complex skills
- May require more user input upfront

## Example: From Session to Skill

### Original Session

**User Request**:
```
"What states do I have teammates in? Read the list from the states.txt file. use the filesystem mcp tool"
```

**Session Trajectory**:
1. Assistant attempts to read `/home/user/workspace/my-agent/states.txt`
2. Tool returns error: "Access denied: file is outside allowed directories"
3. Assistant calls `list_allowed_directories` to check permissions
4. Tool returns: `/home/user/workspace/my-agent/cuga_workspace`
5. Assistant searches for file in allowed directory using `search_files`
6. Tool finds: `/home/user/workspace/my-agent/cuga_workspace/states.txt`
7. Assistant reads file from correct location
8. Tool returns: "texas\nnew york\nmassachusetts\n"
9. Assistant formats and presents results to user

**Outcome**: Successfully read file and listed teammate locations

### Generated Skill

```markdown
---
name: read-file-with-permission-check
description: Read a file from workspace, handling permission errors by checking allowed directories and searching for the file
---

# Read File with Permission Check

## Overview

This skill reads a text file from a workspace directory, automatically handling permission errors by checking allowed directories and searching for the file in the correct location. Useful when file locations are uncertain or when working with MCP filesystem tools that have directory restrictions.

## Parameters

- **filename**: The name of the file to read (e.g., "states.txt", "config.json")
- **workspace_name**: The name of the workspace directory (e.g., "cuga_workspace")
- **expected_path**: (Optional) The initial path to try reading from

## Workflow

### Step 1: Attempt Initial Read

Try to read the file from the expected location.

**Action**: Use `mcp__filesystem__read_text_file`

**Example**:
```
mcp__filesystem__read_text_file({
  "path": "{expected_path}/{filename}"
})
```

### Step 2: Handle Permission Error

If access is denied, check what directories are allowed.

**Action**: Use `mcp__filesystem__list_allowed_directories`

**Example**:
```
mcp__filesystem__list_allowed_directories({})
```

### Step 3: Search for File

Search for the file in the allowed directories.

**Action**: Use `mcp__filesystem__search_files`

**Example**:
```
mcp__filesystem__search_files({
  "path": "{allowed_directory}",
  "pattern": "{filename}"
})
```

### Step 4: Read from Correct Location

Read the file from the location found in the search.

**Action**: Use `mcp__filesystem__read_text_file`

**Example**:
```
mcp__filesystem__read_text_file({
  "path": "{found_path}"
})
```

### Step 5: Parse and Present Results

Parse the file contents and present them to the user in a clear format.

## Error Handling

**File Not Found**:
- If search returns no results, inform user the file doesn't exist in allowed directories
- Suggest checking the filename or workspace location

**Multiple Files Found**:
- If search returns multiple matches, list all locations
- Ask user which one to read

**Empty File**:
- If file is empty, inform user clearly
- Don't treat as an error

## Examples

### Example 1: Reading Team Locations

**Input**:
- filename: "states.txt"
- workspace_name: "cuga_workspace"
- expected_path: "/home/user/workspace/my-agent"

**Workflow**:
1. Try to read from expected path → Access denied
2. Check allowed directories → `/home/user/workspace/my-agent/cuga_workspace`
3. Search for "states.txt" in allowed directory → Found
4. Read file → "texas\nnew york\nmassachusetts\n"
5. Present: "You have teammates in: Texas, New York, Massachusetts"

**Expected Output**:
```
You have teammates in:
- Texas
- New York
- Massachusetts
```

### Example 2: Reading Configuration File

**Input**:
- filename: "config.json"
- workspace_name: "project_workspace"

**Expected Output**:
```json
{
  "setting1": "value1",
  "setting2": "value2"
}
```

## Notes

- This pattern is particularly useful with MCP filesystem tools that have directory restrictions
- Always check allowed directories first if you encounter permission errors
- The search step helps locate files even when the exact path is unknown
- Consider caching the allowed directories list if making multiple file operations
```

### Comparison: Session vs Skill

| Aspect | Original Session | Generated Skill |
|--------|-----------------|-----------------|
| **Specificity** | Hardcoded paths: `/home/user/workspace/my-agent/states.txt` | Parameterized: `{expected_path}/{filename}` |
| **Error Handling** | Reactive: encountered error, then adapted | Proactive: anticipates permission errors |
| **Reusability** | Single use case: reading states.txt | General pattern: reading any file with permission handling |
| **Documentation** | Implicit in conversation | Explicit workflow steps and examples |
| **Tool Calls** | Specific arguments | Template arguments with placeholders |

## Design Decisions (User Confirmed)

1. **Parameterization Level**: **Conservative**
   - Only parameterize obvious session-specific values (file paths, specific names, user data)
   - Keep tool names, general patterns, and workflow structure concrete
   - User can manually edit the skill later for more customization

2. **Skill Location**: **~/.claude/skills/{skill-name}/**
   - Save to user's home directory under .claude/skills
   - Makes skills available across all projects
   - Path: `~/.claude/skills/{skill-name}/SKILL.md`
   - If scripts generated: `~/.claude/skills/{skill-name}/scripts/*.py`

3. **Skill Format**: **SKILL.md + Helper Scripts**
   - Generate comprehensive SKILL.md documentation
   - Include workflow steps, parameters, examples, and error handling
   - **NEW**: Generate Python helper scripts for programmatic operations
   - Scripts are optional - only generated when workflow includes data processing, file operations, API calls, etc.

4. **Validation**: **No automatic validation**
   - Generate and save the skill directly
   - User will review and test the skill when they use it
   - Faster workflow, user has full control

## Helper Script Examples

### Example 1: File Parser Script

**Use Case**: Workflow involves reading and parsing a structured file

```python
#!/usr/bin/env python3
"""
Parse a text file and convert to JSON format.

Usage:
    python3 parse_file.py <input_file> [--format json|csv]

Arguments:
    input_file: Path to the file to parse
    --format: Output format (default: json)
"""

import sys
import json
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Parse file and convert to JSON")
    parser.add_argument("input_file", help="Path to the input file")
    parser.add_argument("--format", choices=["json", "csv"], default="json",
                       help="Output format")
    
    args = parser.parse_args()
    
    try:
        data = parse_file(args.input_file)
        
        if args.format == "json":
            print(json.dumps(data, indent=2))
        elif args.format == "csv":
            print_csv(data)
            
    except FileNotFoundError:
        print(f"Error: File not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def parse_file(filepath):
    """Parse the input file and return structured data."""
    with open(filepath, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]
    
    return {
        "items": lines,
        "count": len(lines)
    }


def print_csv(data):
    """Print data in CSV format."""
    for item in data.get("items", []):
        print(item)


if __name__ == "__main__":
    main()
```

### Example 2: API Client Script

**Use Case**: Workflow involves making HTTP requests to an API

```python
#!/usr/bin/env python3
"""
Fetch data from an API endpoint.

Usage:
    python3 api_client.py <endpoint> [--method GET|POST] [--data DATA]

Arguments:
    endpoint: API endpoint URL
    --method: HTTP method (default: GET)
    --data: JSON data for POST requests
"""

import sys
import json
import argparse
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser(description="API client for fetching data")
    parser.add_argument("endpoint", help="API endpoint URL")
    parser.add_argument("--method", choices=["GET", "POST"], default="GET",
                       help="HTTP method")
    parser.add_argument("--data", help="JSON data for POST requests")
    
    args = parser.parse_args()
    
    try:
        result = fetch_data(args.endpoint, args.method, args.data)
        print(json.dumps(result, indent=2))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_data(endpoint, method="GET", data=None):
    """Fetch data from the API endpoint."""
    headers = {"Content-Type": "application/json"}
    
    if method == "POST" and data:
        data = json.dumps(json.loads(data)).encode('utf-8')
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
    else:
        req = urllib.request.Request(endpoint, headers=headers)
    
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))


if __name__ == "__main__":
    main()
```

### Example 3: Data Validator Script

**Use Case**: Workflow involves validating input data

```python
#!/usr/bin/env python3
"""
Validate data against a schema.

Usage:
    python3 validate_data.py <data_file> [--schema SCHEMA_FILE]

Arguments:
    data_file: Path to the data file to validate
    --schema: Path to schema file (optional)
"""

import sys
import json
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Validate data against schema")
    parser.add_argument("data_file", help="Path to the data file")
    parser.add_argument("--schema", help="Path to schema file")
    
    args = parser.parse_args()
    
    try:
        with open(args.data_file, 'r') as f:
            data = json.load(f)
        
        if args.schema:
            with open(args.schema, 'r') as f:
                schema = json.load(f)
            is_valid, errors = validate_with_schema(data, schema)
        else:
            is_valid, errors = validate_basic(data)
        
        if is_valid:
            print(json.dumps({"valid": True, "message": "Data is valid"}))
            sys.exit(0)
        else:
            print(json.dumps({"valid": False, "errors": errors}, indent=2))
            sys.exit(1)
            
    except FileNotFoundError as e:
        print(f"Error: File not found: {e.filename}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def validate_basic(data):
    """Perform basic validation checks."""
    errors = []
    
    if not isinstance(data, dict):
        errors.append("Data must be a JSON object")
    
    return len(errors) == 0, errors


def validate_with_schema(data, schema):
    """Validate data against a schema."""
    errors = []
    
    required_fields = schema.get("required", [])
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
    
    return len(errors) == 0, errors


if __name__ == "__main__":
    main()
```

### Example 4: File Transformer Script

**Use Case**: Workflow involves transforming file formats

```python
#!/usr/bin/env python3
"""
Transform file from one format to another.

Usage:
    python3 transform_file.py <input_file> <output_file> [--from FORMAT] [--to FORMAT]

Arguments:
    input_file: Path to the input file
    output_file: Path to the output file
    --from: Input format (json, csv, txt)
    --to: Output format (json, csv, txt)
"""

import sys
import json
import csv
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Transform file formats")
    parser.add_argument("input_file", help="Path to input file")
    parser.add_argument("output_file", help="Path to output file")
    parser.add_argument("--from", dest="from_format", default="json",
                       choices=["json", "csv", "txt"],
                       help="Input format")
    parser.add_argument("--to", dest="to_format", default="json",
                       choices=["json", "csv", "txt"],
                       help="Output format")
    
    args = parser.parse_args()
    
    try:
        # Read input
        data = read_file(args.input_file, args.from_format)
        
        # Write output
        write_file(args.output_file, data, args.to_format)
        
        print(f"Successfully transformed {args.input_file} to {args.output_file}")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def read_file(filepath, format_type):
    """Read file in specified format."""
    if format_type == "json":
        with open(filepath, 'r') as f:
            return json.load(f)
    elif format_type == "csv":
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            return list(reader)
    elif format_type == "txt":
        with open(filepath, 'r') as f:
            return [line.strip() for line in f if line.strip()]


def write_file(filepath, data, format_type):
    """Write data in specified format."""
    if format_type == "json":
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    elif format_type == "csv":
        if not data:
            return
        with open(filepath, 'w', newline='') as f:
            if isinstance(data[0], dict):
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)
            else:
                writer = csv.writer(f)
                writer.writerows([[item] for item in data])
    elif format_type == "txt":
        with open(filepath, 'w') as f:
            for item in data:
                f.write(f"{item}\n")


if __name__ == "__main__":
    main()
```

## Next Steps

1. ✅ Design approved by user
2. ✅ Trajectory access method confirmed (use session context)
3. Create the actual save-session-as-skill SKILL.md
4. Test the skill with the current session as an example