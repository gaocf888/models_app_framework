#!/bin/sh
# Cython-compile /workspace/app inside the compiled Docker builder.
# PYTHON must be the same interpreter used at runtime (conda on 沐曦, CPython in nvidia image).
set -eu

PYTHON="${PYTHON:-python}"
WORKSPACE="${1:-/workspace}"
APP_DIR="${2:-${WORKSPACE}/app}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
KEEP_FILE="${COMPILE_KEEP_FILE:-${SCRIPT_DIR}/compile_keep_py.txt}"

if ! "${PYTHON}" -c "import Cython" >/dev/null 2>&1; then
  echo "[compile_app] Cython is not installed for ${PYTHON}" >&2
  exit 1
fi

exec "${PYTHON}" "${SCRIPT_DIR}/compile_app.py" "${WORKSPACE}" --app-dir "${APP_DIR}" --keep-file "${KEEP_FILE}"
