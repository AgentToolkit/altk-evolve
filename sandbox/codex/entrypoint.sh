#!/usr/bin/env bash
set -euo pipefail

codex_home="${CODEX_HOME:-${HOME:-/codex-home}}"

mkdir -p "${codex_home}"
export HOME="${HOME:-${codex_home}}"
export CODEX_HOME="${codex_home}"

python3 /usr/local/bin/bootstrap_codex_config.py

exec "$@"
