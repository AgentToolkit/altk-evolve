---
id: 6719e8cb6a4a
type: guideline
trigger: Needing to download and run a bootstrap script on a minimal container that may lack curl and wget.
agent: bob
tags: [pip, python, urllib, get-pip, minimal-env, container]
sources:
  - trajectories/93c78e3d-openai-chat-completions.analysis.json
related_summary: summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md
---

# Bootstrap pip with urllib when curl and wget absent

To reinstall a missing pip, fetch get-pip.py with Python's own standard library rather than reaching for curl or wget, which are frequently absent from minimal images: `python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')"` then `python3 /tmp/get-pip.py`. This needs only the interpreter you are already trying to repair.

## Rationale

Piping get-pip.py through `curl ... | python3` or `wget -qO- ... | python3` fails outright when those binaries are not installed, wasting retries. `urllib.request` ships with CPython, so if `python3` runs at all the download path is guaranteed to exist, and get-pip.py rebuilds the full pip package (not just metadata).

## Sources

- [trajectory summary](../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)
- [normalized JSON](trajectories/93c78e3d-openai-chat-completions.analysis.json)
