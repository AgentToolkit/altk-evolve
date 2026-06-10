#!/usr/bin/env bash
# Convert a CSV to Parquet on a minimal PEP 668 Debian/Ubuntu host.
# Usage: csv_to_parquet.sh <input.csv> <output.parquet>
# Installs pandas via apt; installs pyarrow via pip --break-system-packages
# (it is not packaged in apt). Idempotent: skips installs already present.
set -euo pipefail
IN="${1:?usage: csv_to_parquet.sh <input.csv> <output.parquet>}"
OUT="${2:?usage: csv_to_parquet.sh <input.csv> <output.parquet>}"

if ! python3 -c 'import pandas' 2>/dev/null; then
  apt update
  apt install -y python3-pandas python3-pip
fi

if ! python3 -c 'import pyarrow' 2>/dev/null; then
  # pyarrow is not in apt; PEP 668 blocks a plain pip install.
  python3 -m pip install --break-system-packages pyarrow
fi

python3 -c "import pandas as pd; pd.read_csv('${IN}').to_parquet('${OUT}', engine='pyarrow', index=False)"
ls -lh "${OUT}"
