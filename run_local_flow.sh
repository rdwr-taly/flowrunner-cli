#!/bin/bash

usage() {
  echo "Usage: $0 <path_to_flow_file.json> [flow_target_url] [sim_users] [debug_level] [cycle_delay_ms] [--min-step-ms N] [--max-step-ms N] [--run-once]" >&2
}

POSITIONAL=()
MIN_STEP_MS=""
MAX_STEP_MS=""
RUN_ONCE="false"

while [[ $# -gt 0 ]]; do
  case $1 in
    --min-step-ms)
      MIN_STEP_MS="$2"
      shift 2
      ;;
    --max-step-ms)
      MAX_STEP_MS="$2"
      shift 2
      ;;
    --run-once)
      RUN_ONCE="true"
      shift
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

if [ -z "$1" ]; then
  usage
  exit 1
fi

FLOW_FILE="$1"
FLOW_URL="${2:-http://localhost:8000}"
SIM_USERS="${3:-1}"
DEBUG_LEVEL="${4:-INFO}"
CYCLE_DELAY_MS="$5"

SCRIPT_DIR="$(dirname "$0")"
PYTHON_SCRIPT="$SCRIPT_DIR/flow_runner_direct_invoker.py"

CMD=(python3 "$PYTHON_SCRIPT" "$FLOW_FILE" "$FLOW_URL" "$SIM_USERS" "$DEBUG_LEVEL")

if [ -n "$CYCLE_DELAY_MS" ]; then
  CMD+=(--cycle-delay-ms "$CYCLE_DELAY_MS")
fi
if [ -n "$MIN_STEP_MS" ]; then
  CMD+=(--min-step-ms "$MIN_STEP_MS")
fi
if [ -n "$MAX_STEP_MS" ]; then
  CMD+=(--max-step-ms "$MAX_STEP_MS")
fi
if [ "$RUN_ONCE" = "true" ]; then
  CMD+=(--run-once)
fi

"${CMD[@]}"
