#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${BASE_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d "${VENV_DIR}" ]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install "musicdl>=2.10.2" "pycryptodomex>=3.20.0" PyYAML >/dev/null

echo "Installed music-orchestrator dependencies into ${VENV_DIR}"
