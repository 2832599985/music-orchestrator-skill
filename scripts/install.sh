#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"${PYTHON_BIN}" - <<'PY'
import sys
print(f"music-orchestrator install check ok: python {sys.version.split()[0]}")
PY

echo "No extra runtime dependencies are required. The skill runs with system python3."
