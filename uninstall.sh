#!/bin/sh
# notebooklm-t3 POSIX 薄卸载入口：调用 install.py --uninstall
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/install.sh" --uninstall "$@"
