#!/usr/bin/env bash
# Re-bootstrap pip from get-pip.py using stdlib urllib (no curl/wget needed).
# Usage: bootstrap_pip.sh [python-interpreter]   (default: python3)
set -euo pipefail
PY="${1:-python3}"
"$PY" -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')"
"$PY" /tmp/get-pip.py
"$PY" -m pip --version
