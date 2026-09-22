#!/bin/sh
# Wrapper kept in this directory as specified by 吉泰方案 §2.3.
exec "$(CDPATH= cd -- "$(dirname -- "$0")/../compiled-common" && pwd)/compile_app.sh" "$@"
