---
id: 224dabd0bcb9
type: guideline
trigger: A system Python where pip imports fail even though pip appears "installed", inside a container you can freely modify.
agent: bob
tags: [pip, python, site-packages, dist-info, ensurepip, container]
sources:
  - trajectories/93c78e3d-openai-chat-completions.analysis.json
related_summary: summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md
---

# Verify the pip module dir not just dist-info

When `pip3` or `python3 -m pip` fails with `ModuleNotFoundError: No module named 'pip'`, list site-packages and check for the actual `pip/` package directory, not just `pip-<ver>.dist-info`. A lingering dist-info with the module dir deleted makes `ensurepip --upgrade` report "Requirement already satisfied" and makes `pip install --force-reinstall pip` fail with "No module named pip" — both refuse to do real work because they trust the leftover metadata. Once you confirm the module dir is gone, skip those two and reinstall from get-pip.py instead.

## Rationale

ensurepip and pip-on-pip both key off the recorded distribution metadata. With dist-info present but the importable package missing, ensurepip short-circuits as a no-op and `python3 -m pip` cannot even bootstrap itself, so every retry loops on the same error until you bypass both with an external bootstrapper.

## Sources

- [trajectory summary](../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)
- [normalized JSON](trajectories/93c78e3d-openai-chat-completions.analysis.json)
