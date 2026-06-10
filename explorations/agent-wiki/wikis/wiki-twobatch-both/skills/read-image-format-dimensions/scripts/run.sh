#!/usr/bin/env bash
set -euo pipefail
if [ $# -ne 1 ]; then
  echo "usage: $0 <image-path>" >&2
  exit 2
fi
exec python3 "$(dirname "$0")/read_dim.py" "$1"
