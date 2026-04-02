# Testing the Evolve Full Mode with MCP

This guide explains how to verify that the Evolve mode is working correctly in Bob (Roo Code) when using full mode with MCP server integration.

## Test the Workflow

To ensure the custom instructions are working correctly, try giving the Evolve mode a simple, generic task.

1. Select the **Evolve** mode from the dropdown in Bob.
2. Enter a prompt like:
   * `"Create a simple python script that prints hello world. Complete the task as fast as possible."`
3. **Observe the Agent's Behavior:**
   * **CORRECT BEHAVIOR:** The agent *must* first attempt to use the `get_guidelines` MCP tool. After writing the script, it *must* attempt to use the `save_trajectory` tool and ask for your permission to proceed *before* calling its built-in `attempt_completion` tool.
   * **INCORRECT BEHAVIOR:** If the agent simply writes the script and finishes the task without calling the MCP tools, the prompt instructions are not being respected, or the MCP server is not connected. 

## Test the Rejection Logic

1. If the agent tries to call `attempt_completion` without first calling `save_trajectory`, you should **Reject** the completion and remind it: *"You forgot to call save_trajectory first as per your instructions."*
2. A successful test means the agent obeys the workflow described in its `roleDefinition` perfectly.