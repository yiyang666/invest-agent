#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
(sleep 2; open 'http://127.0.0.1:8765') &
exec "$ROOT/scripts/run_local_web.sh"
