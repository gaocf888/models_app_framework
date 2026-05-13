#!/usr/bin/env bash
set -euo pipefail
mkdir -p /io/.cache /io/layout-output /io/.paddleocr
export PADDLEOCR_HOME="${PADDLEOCR_HOME:-/io/.paddleocr}"
exec "$@"
