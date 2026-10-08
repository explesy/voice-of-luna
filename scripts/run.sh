#!/usr/bin/env bash
# Voice of Lúna — Developer Launcher with Preflight Diagnostics
# Usage:
#   ./scripts/run.sh [OPTIONS]
#
# Options:
#   --check-only     Run preflight checks and exit without starting the server
#   --no-preflight   Skip preflight checks and boot uvicorn directly
#   --strict         Fail preflight if any warnings are detected
#   --host <HOST>    Host interface to bind (default: 127.0.0.1)
#   --port <PORT>    Port to listen on (default: 8000 or $PORT)
#   --no-reload      Disable auto-reload for production-like runs
#   -h, --help       Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
RELOAD="--reload"
RUN_PREFLIGHT=true
PREFLIGHT_STRICT=false
CHECK_ONLY=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=true
      shift
      ;;
    --no-preflight)
      RUN_PREFLIGHT=false
      shift
      ;;
    --strict)
      PREFLIGHT_STRICT=true
      shift
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --no-reload)
      RELOAD=""
      shift
      ;;
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Run '$0 --help' for usage." >&2
      exit 1
      ;;
  esac
done

cd "${BACKEND_DIR}"

if [ "${RUN_PREFLIGHT}" = true ]; then
  PREFLIGHT_ARGS=()
  if [ "${PREFLIGHT_STRICT}" = true ]; then
    PREFLIGHT_ARGS+=("--strict")
  fi
  if [ "${CHECK_ONLY}" = true ]; then
    PREFLIGHT_ARGS+=("--check-only")
  fi

  export PORT="${PORT}"
  if ! uv run python scripts/preflight.py "${PREFLIGHT_ARGS[@]}"; then
    echo -e "\033[31m\033[1m[Launcher] Preflight checks failed. Aborting startup.\033[0m" >&2
    echo -e "\033[33mHint: Use --no-preflight to bypass diagnostic checks.\033[0m\n" >&2
    exit 1
  fi

  if [ "${CHECK_ONLY}" = true ]; then
    exit 0
  fi
fi

echo -e "\033[1m\033[36m🚀 Starting Voice of Lúna at http://${HOST}:${PORT}\033[0m"
echo -e "\033[2m   Localhost single-user mode · Press Ctrl+C to stop.\033[0m\n"

exec uv run uvicorn app.main:app --host "${HOST}" --port "${PORT}" ${RELOAD}
