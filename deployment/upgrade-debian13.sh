#!/usr/bin/env bash
# Compatibility entry point retained for deploy-kits published before v1.3.2.
set -euo pipefail
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec bash "$SCRIPT_DIR/upgrade.sh" "$@"
