#!/bin/bash
# Simple launcher: sets up venv (if missing), installs deps, starts API, and opens the frontend.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Pick a Python executable.
PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  PYTHON="$(command -v python || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3 not found. Please install it from https://www.python.org/downloads/ and retry."
  exit 1
fi

echo "Using Python: $PYTHON"

# Create venv if missing.
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv)..."
  "$PYTHON" -m venv .venv
fi

# Activate venv.
source ".venv/bin/activate"

# Install deps.
echo "Installing requirements..."
pip install --upgrade pip >/dev/null
pip install -r requirements.txt >/dev/null

# Start server.
PORT=8000
LOG_FILE="$ROOT/server.log"
echo "Starting API on http://localhost:$PORT ..."
uvicorn main:app --reload --port "$PORT" >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

# Give server a moment.
sleep 2

# Open frontend.
if command -v open >/dev/null; then
  open "$ROOT/index.html"
else
  echo "Please open $ROOT/index.html in your browser."
fi

echo "Server PID: $SERVER_PID (logs: $LOG_FILE)"
echo "Press Ctrl+C in this window to stop the server."

# Keep script running until user stops it, and clean up server on exit.
trap "echo 'Stopping server...'; kill $SERVER_PID" EXIT
wait $SERVER_PID
