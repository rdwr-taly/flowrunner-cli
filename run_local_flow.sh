#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <path_to_flow_file.json> [flow_target_url] [sim_users] [debug_level]" >&2
  exit 1
fi

FLOW_FILE="$1"
FLOW_URL="${2:-http://localhost:8000}"
SIM_USERS="${3:-1}"
DEBUG_LEVEL="${4:-INFO}"

SCRIPT_DIR="$(dirname "$0")"
PYTHON_SCRIPT="$SCRIPT_DIR/flow_runner_direct_invoker.py"

python3 "$PYTHON_SCRIPT" "$FLOW_FILE" "$FLOW_URL" "$SIM_USERS" "$DEBUG_LEVEL"
