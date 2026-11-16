#!/bin/sh
set -eu

CDPATH= cd "$(dirname "$0")/.."

.venv/bin/python -m pytest -q tests
PYTHONPATH="web/backend:.${PYTHONPATH:+:$PYTHONPATH}" .venv/bin/python -m pytest -q web/tests
(cd packages/osm-manager && ../../.venv/bin/python -m pytest -q tests)
(cd packages/osm-routing && ../../.venv/bin/python -m pytest -q tests)
node --test web/tests/*.test.mjs
