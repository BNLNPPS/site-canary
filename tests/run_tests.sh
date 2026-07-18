#!/bin/bash
# Run the canary functionality tests with the project venv if present.
set -e
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" tests/test_basic.py
