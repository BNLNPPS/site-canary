#!/bin/bash
# Build the canary probe sandbox from what is deployed: the canary package
# as the release's wheel with its dependencies vendored into kit/, the
# prmon static binary, and the release's production in-job runner. The
# probe job needs no network installs: everything it runs arrives in this
# archive. Nothing here reads a development checkout; the overrides exist
# for running the doer by hand against another tree.
#
# Usage: bash build-sandbox.sh <workdir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:?usage: build-sandbox.sh <workdir>}"
RELEASE="${SWF_MONITOR_RELEASE:-/opt/swf-monitor/current}"

mkdir -p "${WORK}/kit"
# The vendoring needs a modern pip (pyproject-only package); the agent's
# minimal PATH resolves python3 to the system 3.6, so use the release's
# interpreter. Override with CANARY_PIP_PYTHON.
PYBIN="${CANARY_PIP_PYTHON:-${RELEASE}/.venv/bin/python}"

# The canary package as deployed: the wheel the deploy builds beside the
# release. CANARY_KIT_SOURCE overrides with any pip-installable source.
KIT_SOURCE="${CANARY_KIT_SOURCE:-}"
if [ -z "${KIT_SOURCE}" ]; then
    KIT_SOURCE="$(ls -1t "${RELEASE}"/wheels/site_canary-*.whl 2>/dev/null | head -1)"
fi
if [ -z "${KIT_SOURCE}" ] || [ ! -e "${KIT_SOURCE}" ]; then
    echo "canary wheel not found under ${RELEASE}/wheels (set CANARY_KIT_SOURCE)" >&2
    exit 1
fi
"${PYBIN}" -m pip install --quiet --target "${WORK}/kit" "${KIT_SOURCE}"

# prmon: the static binary, cached once under the agent-writable shared
# tree. CANARY_PRMON overrides with a binary path.
PRMON="${CANARY_PRMON:-}"
if [ -z "${PRMON}" ]; then
    PRMON_DIR="${SWF_TMP_DIR:-/data/swf-tmp}/canary-prmon"
    if [ ! -x "${PRMON_DIR}/bin/prmon" ]; then
        bash "${HERE}/fetch_prmon.sh" "${PRMON_DIR}" >&2
    fi
    PRMON="${PRMON_DIR}/bin/prmon"
fi
if [ ! -x "${PRMON}" ]; then
    echo "prmon binary not found at ${PRMON}" >&2
    exit 1
fi
cp "${PRMON}" "${WORK}/kit/prmon"
chmod +x "${WORK}/kit/prmon"

# The in-job runner: the same evgen_job_dispatcher.py production jobs
# run, taken from the release (one runner for production and probe
# jobs); its canary branch executes the landing kit. Override with
# CANARY_DISPATCHER.
DISPATCHER="${CANARY_DISPATCHER:-${RELEASE}/scripts/evgen_job_dispatcher.py}"
if [ ! -f "${DISPATCHER}" ]; then
    echo "dispatcher not found: ${DISPATCHER}" >&2
    exit 1
fi
cp "${DISPATCHER}" "${WORK}/evgen_job_dispatcher.py"

tar -C "${WORK}" -czf "${WORK}/canary-kit.tgz" kit
echo "sandbox: ${WORK}/canary-kit.tgz ($(du -h "${WORK}/canary-kit.tgz" | cut -f1)) from ${KIT_SOURCE}"
