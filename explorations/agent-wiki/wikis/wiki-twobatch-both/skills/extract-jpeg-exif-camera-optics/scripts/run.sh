#!/usr/bin/env bash
set -euo pipefail
if [ $# -ne 2 ]; then
  echo "usage: $0 <jpeg-path> <tag-id-hex>" >&2
  exit 2
fi
exec python3 "$(dirname "$0")/extract.py" "$1" "$2"
