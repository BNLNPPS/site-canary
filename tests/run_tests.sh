#!/bin/bash
# Run the canary functionality tests with the active venv, else the
# project venv if present, else python3. The active venv is named by
# absolute path: an environment file that prepends a directory holding a
# python3 shim (the swf-testbed ~/.env does) would otherwise win over it.
set -e
cd "$(dirname "$0")/.."
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
[ -n "$VIRTUAL_ENV" ] && [ -x "$VIRTUAL_ENV/bin/python" ] && PY="$VIRTUAL_ENV/bin/python"
"$PY" tests/test_basic.py || exit 1
"$PY" tests/test_guard.py || exit 1
exec "$PY" tests/test_nodes.py
