#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
exec "$ROOT/.conda-env/bin/python" -m invest_agent.web.server "$@"
