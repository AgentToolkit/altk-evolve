---
name: kaizen-recall
description: Retrieves relevant entities from a knowledge base to inject context-appropriate best practices before task execution.
---

# Kaizen Recall Skill

This skill retrieves relevant guidelines before beginning any investigation, design, or coding work.

## Section 1: At Task Start (Retrieve Guidelines)

Before beginning your work for the user, you must fetch existing guidelines related to the user's request.

**Command to run:**
```bash
python3 <path-to-skill-dir>/scripts/get.py --type guideline --task "<brief summary of the user's goal>"
```
(If `python3` is not found, use `python` instead)

**⚠️ REQUIRED PARAMETERS:**
- `--type guideline` - MUST be included (specifies entity type to retrieve)
- `--task "..."` - MUST be included (brief summary of user's goal)

**Common Error:**
If you see `error: the following arguments are required: --type`, you forgot to include `--type guideline` in the command.

**How to use the output:**
The script will return a list of guidelines in JSON format (or plain text). Review these carefully. They represent hard-learned lessons or organizational standards. Incorporate them into your approach to the current task. If no guidelines are found, proceed normally.

