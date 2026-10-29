#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load the Sophon runtime environment when the board provides it globally.
set +u
if [[ -f /etc/profile ]]; then
  # shellcheck disable=SC1091
  source /etc/profile >/dev/null 2>&1 || true
fi
set -u
export PATH="$HOME/.local/bin:/opt/sophon/libsophon-current/bin:$PATH"

PYTHON_BIN=""
for candidate in python3.10 python3.8 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import numpy; import cv2; import sophon.sail' >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "ERROR: no Python environment with numpy, cv2 and sophon.sail was found." >&2
  exit 2
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/validate.py" "$@"
