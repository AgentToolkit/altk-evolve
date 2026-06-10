#!/usr/bin/env bash
# Persist a jq run as a reproducible cmd.sh + jq_args.yaml.
# Usage:
#   persist_jq_args.sh '<FILTER>' '<INPUT>' '<OUTPUT>' '<CMD_SH>' '<YAML>' [jq-flag ...]
# Each jq-flag is one complete argument, e.g. '--indent 2' or '--tab'.
# The filter must contain ONLY the filter expression (no file paths,
# no redirection). args[] holds the flags; the checker reconstructs:
#   jq $(yq -r '.args[]' YAML) "$(yq -r '.filter' YAML)" INPUT > OUTPUT
set -euo pipefail
filter=$1; input=$2; output=$3; cmd_sh=$4; yaml=$5; shift 5
flags=("$@")

# cmd.sh: the full command, flags + quoted filter + io redirection.
{
  printf 'jq'
  for f in "${flags[@]}"; do printf ' %s' "$f"; done
  # single-quote the filter for the shell; escape any embedded single quotes.
  esc=${filter//\'/\'\\\'\'}
  printf " '%s' %s > %s\n" "$esc" "$input" "$output"
} > "$cmd_sh"

# jq_args.yaml: filter as a YAML double-quoted scalar, args as a block list.
{
  # escape backslashes then double-quotes for the YAML double-quoted scalar.
  y=${filter//\\/\\\\}
  y=${y//\"/\\\"}
  printf 'filter: "%s"\n' "$y"
  printf 'args:\n'
  for f in "${flags[@]}"; do printf '  - "%s"\n' "$f"; done
} > "$yaml"

echo "wrote $cmd_sh and $yaml"
