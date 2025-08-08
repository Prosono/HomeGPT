#!/usr/bin/env bash
set -euo pipefail

export OPTIONS_FILE=/data/options.json
export SUPERVISOR_API="http://supervisor/core/api"

if [[ ! -f "$OPTIONS_FILE" ]]; then
  echo "Missing options.json" >&2
  exit 1
fi

export OPENAI_API_KEY=$(jq -r '.openai_api_key' $OPTIONS_FILE)
export MODEL=$(jq -r '.model' $OPTIONS_FILE)
export MODE=$(jq -r '.mode' $OPTIONS_FILE)
export SUMMARIZE_TIME=$(jq -r '.summarize_time' $OPTIONS_FILE)
export CONTROL_ALLOWLIST=$(jq -c '.control_allowlist' $OPTIONS_FILE)
export MAX_ACTIONS_PER_HOUR=$(jq -r '.max_actions_per_hour' $OPTIONS_FILE)
export DRY_RUN=$(jq -r '.dry_run' $OPTIONS_FILE)
export LOG_LEVEL=$(jq -r '.log_level' $OPTIONS_FILE)
export LANGUAGE=$(jq -r '.language' $OPTIONS_FILE)

# Supervisor provides this automatically because homeassistant_api: true
if [[ -z "${SUPERVISOR_TOKEN:-}" ]]; then
  echo "Missing SUPERVISOR_TOKEN env from Supervisor." >&2
  exit 1
fi

python /app/run.py