#!/usr/bin/env bash
set -euo pipefail

PORT="${FREECAD_MCP_SSE_PORT:-8099}"
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      if [[ $# -lt 2 ]]; then
        echo "error: --port requires a value" >&2
        exit 1
      fi
      PORT="$2"
      ARGS+=("$1" "$2")
      shift 2
      ;;
    --port=*)
      PORT="${1#*=}"
      ARGS+=("$1")
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

PIDS=$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)

if [[ -n "${PIDS}" ]]; then
  echo "Detected processes listening on port ${PORT}: ${PIDS}" >&2
  echo "Sending SIGTERM..." >&2
  # shellcheck disable=SC2086
  kill ${PIDS} || true
  sleep 0.5

  STILL_RUNNING=$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "${STILL_RUNNING}" ]]; then
    echo "Processes still bound to port ${PORT}: ${STILL_RUNNING}" >&2
    echo "Sending SIGKILL..." >&2
    # shellcheck disable=SC2086
    kill -9 ${STILL_RUNNING} || true
    sleep 0.2
  fi
fi

if lsof -iTCP:"${PORT}" -sTCP:LISTEN &>/dev/null; then
  echo "Unable to free port ${PORT}. Aborting." >&2
  exit 1
fi

echo "Port ${PORT} is free. Starting freecad-mcp-sse..." >&2
#exec uv run freecad-mcp-sse "${ARGS[@]}"
