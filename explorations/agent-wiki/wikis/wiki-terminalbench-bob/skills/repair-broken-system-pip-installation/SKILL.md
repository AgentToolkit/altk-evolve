---
id: skill:repair-broken-system-pip-installation
type: skill
name: repair-broken-system-pip-installation
description: "Repair a broken system-wide pip when `pip3`/`python3 -m pip` reports `No module named 'pip'`: read the whole traceback, confirm the pip module directory is missing (only dist-info present), then re-bootstrap pip by fetching get-pip.py via Python's stdlib urllib when curl and wget are unavailable."
trigger: "A Python install where pip is broken — `pip3 --version` or `python3 -m pip` fails with ModuleNotFoundError: No module named 'pip', often inside a minimal container that lacks curl/wget. ensurepip claims pip is already satisfied but pip still won't import."
agent: bob
sources:
  - trajectories/93c78e3d-openai-chat-completions.analysis.json
related_summary: summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md
verified_at: 2026-06-09
tags: [pip, python, site-packages, dist-info, ensurepip, container, urllib, get-pip, minimal-env, debugging, traceback, shebang, diagnosis]
---

# Repair Broken System Pip Installation

## Overview

Restore a usable system pip after `No module named 'pip'`. The wrapper script and dist-info can survive while the actual `pip` package directory under site-packages is gone, so `ensurepip` reports pip 'already satisfied' yet nothing imports. The reliable fix is to re-bootstrap pip from get-pip.py, downloaded with stdlib urllib when no HTTP CLI is installed.

## When To Use

- `pip3 --version` raises `ModuleNotFoundError: No module named 'pip'` from the wrapper script, or `python3 -m pip` prints `No module named pip`.
- `python3 -m ensurepip --upgrade` says pip is already satisfied but pip still cannot be imported — the dist-info exists but the `pip/` module dir is missing from site-packages.
- The environment is minimal (e.g. a container) and `curl`/`wget` are not installed, so the usual `curl … get-pip.py | python3` recipe fails.

## Workflow

1. Read the full traceback first. `ModuleNotFoundError: No module named 'pip'` from `/usr/local/bin/pip3` means the wrapper exists but the pip package can't be imported — do not assume the shebang/interpreter is the problem.
2. Confirm the diagnosis: `ls /usr/local/lib/pythonX.Y/site-packages/ | grep -i pip`. If you see only `pip-<ver>.dist-info` and no `pip/` directory, the module body is missing even though metadata says it is installed. `ensurepip` will falsely report 'already satisfied' in this state.
3. Do not waste turns editing the pip3 shebang or running `ensurepip`/`pip install --force-reinstall pip` — they cannot work when pip itself is unimportable.
4. Re-bootstrap pip. If curl/wget are absent, fetch get-pip.py with Python's stdlib and run it: `python3 -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')" && python3 /tmp/get-pip.py`. Or run `bash scripts/bootstrap_pip.sh` (pass the python interpreter as $1, default python3).
5. Verify the fix by re-running the originally failing command: `pip3 --version` should now print a real version and path under site-packages/pip.

## Sources

- [trajectory summary](../../summaries/93c78e3d-76ab-4b35-bbe5-c377cc5ad0e3.md)
- [normalized JSON](trajectories/93c78e3d-openai-chat-completions.analysis.json)
