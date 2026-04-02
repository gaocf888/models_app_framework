#!/usr/bin/env sh
set -eu

mkdir -p /io /io/.hf_cache /io/mineru-output

exec "$@"
