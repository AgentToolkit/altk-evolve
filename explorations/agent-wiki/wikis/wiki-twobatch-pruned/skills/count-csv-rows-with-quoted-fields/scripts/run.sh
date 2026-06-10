#!/usr/bin/env bash
set -euo pipefail
if [ $# -ne 1 ]; then
  echo "usage: $0 <csv-path>" >&2
  exit 2
fi
exec python3 "$(dirname "$0")/count.py" "$1"
