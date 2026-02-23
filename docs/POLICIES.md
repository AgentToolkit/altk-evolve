# Policy Support in Kaizen

Kaizen supports storing and managing structured **policies** as entities. This allows agents to retrieve behavioral rules, playbooks, and guards using semantic search.

## Policy Schema

Policies are stored as Kaizen entities with `type="policy"`. The core schema is defined in `kaizen/schema/policy.py`.

### Policy Types
Kaizen supports the following CUGA-compatible policy types:
- `intent_guard`: Blocks forbidden intents and returns specific responses.
- `playbook`: Provides step-by-step Standard Operating Procedures (SOPs).
- `tool_guide`: Enriches tool descriptions with additional context.
- `tool_approval`: Requires human confirmation before executing sensitive tools.
- `output_formatter`: Reformats agent responses.

### Trigger Types
- `keyword`: Matches specific words or phrases.
- `natural_language`: Matches semantic meaning using vector similarity.
- `always`: Applies to every input/response (e.g., for global output formatters).

## Using MCP Tools for Policies

The Kaizen MCP server provides several tools to manage and retrieve policies.

### 1. Creating a Policy
Use `create_entity` with `entity_type="policy"`. The `content` should be the serialized JSON representation of the Policy model.

**Example:**
```json
{
  "name": "Infrastructure Protection",
  "type": "intent_guard",
  "description": "Prevents unauthorized deletion of core infrastructure.",
  "triggers": [
    {
      "type": "natural_language",
      "value": ["delete the database", "wipe out the core system"],
      "target": "intent"
    }
  ],
  "content": "Access Denied: You are not authorized to perform infrastructure deletion.",
  "enabled": true
}
```

### 2. Retrieving Policies
Use `get_entities` with `entity_type="policy"` to fetch relevant policies for a given task.

```bash
# Example call
get_entities(task="I need to clear the production database", entity_type="policy")
```


### 3. Backward Compatibility
The `get_guidelines` tool is maintained for backward compatibility and specifically targets entities of type `guideline`. For policies, use the generic `get_entities` tool.
