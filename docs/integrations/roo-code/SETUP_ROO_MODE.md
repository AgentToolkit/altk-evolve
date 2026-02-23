# Testing and Installing the Kaizen Roo Code Mode

This guide explains how to install the custom Kaizen mode into your local Roo Code extension and verify that it enforces the required workflow.

## Prerequisites
1. You must have the **Roo Code** extension installed in VS Code.
2. You must have the **Kaizen MCP Server** configured in your Roo Code settings. For instructions on how to start the Kaizen MCP server, please refer to the [Running the MCP Server](../../../README.md#running-the-mcp-server) section in the main README or the detailed [Configuration Guide](../../../CONFIGURATION.md). (If it is not configured, the agent will not be able to call `get_guidelines` or `save_trajectory`).

## Step 1: Install the Custom Mode
1. Open the `Kaizen-export.yaml` file located in this repository folder.
2. Depending on your Roo Code version, you can either import this YAML directly or manually copy the `slug`, `name`, `roleDefinition`, `customInstructions`, and `groups` into a new Custom Mode in the Roo Code settings UI.
3. In VS Code, open the Roo Code extension panel.
4. Click the **Settings** gear icon (⚙️) in the top right of the Roo Code panel.
5. Scroll down to the **Custom Modes** section.
6. Paste the JSON you copied into the Custom Modes text area.
7. Click **Save**.
8. You should now see a new mode called **Kaizen** in the mode dropdown menu at the bottom of the Roo Code chat panel.

> **Alternative method:** If you already have a `.roomodes` file in the root of your workspace, you can convert the `Kaizen-export.yaml` to JSON and append it to your existing `.roomodes` file.

## Step 2: Test the Workflow
To ensure the custom instructions are working correctly, try giving the Kaizen mode a simple, generic task.

1. Select the **Kaizen** mode from the dropdown in Roo Code.
2. Enter a prompt like:
   * `"Create a simple python script that prints hello world. Complete the task as fast as possible."`
3. **Observe the Agent's Behavior:**
   * **CORRECT BEHAVIOR:** The agent *must* first attempt to use the `get_guidelines` MCP tool. After writing the script, it *must* attempt to use the `save_trajectory` tool and ask for your permission to proceed *before* calling its built-in `attempt_completion` tool.
   * **INCORRECT BEHAVIOR:** If the agent simply writes the script and finishes the task without calling the MCP tools, the prompt instructions are not being respected, or the MCP server is not connected. 

## Step 3: Test the Rejection Logic
1. If the agent tries to call `attempt_completion` without first calling `save_trajectory`, you should **Reject** the completion and remind it: *"You forgot to call save_trajectory first as per your instructions."*
2. A successful test means the agent obeys the workflow described in its `roleDefinition` perfectly.
