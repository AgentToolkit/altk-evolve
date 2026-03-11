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
python .bob/skills/kaizen-recall/scripts/get.py --type guideline --task "<brief summary of the user's goal>"
```

**How to use the output:**
The script will return a list of guidelines in JSON format (or plain text). Review these carefully. They represent hard-learned lessons or organizational standards. Incorporate them into your approach to the current task. If no guidelines are found, proceed normally.
