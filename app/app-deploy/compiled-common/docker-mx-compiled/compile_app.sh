#!/bin/sh
# Wrapper; Nuitka entry lives in the parent compiled-common/ directory.
exec "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/compile_app.sh" "$@"
