#!/bin/bash
# Build the canary probe sandbox: the canary package with its
# dependencies vendored into kit/ plus the static prmon binary, tarred
# for the PanDA task sandbox. The probe job needs no network installs:
# everything it runs arrives in this archive.
#
# Usage: bash build-sandbox.sh <workdir>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"
WORK="${1:?usage: build-sandbox.sh <workdir>}"

mkdir -p "${WORK}/kit"
# The vendoring needs a modern pip (pyproject-only package); the
# agent's minimal PATH resolves python3 to the system 3.6, so pick an
# explicit interpreter. Override with CANARY_PIP_PYTHON.
PYBIN="${CANARY_PIP_PYTHON:-}"
if [ -z "${PYBIN}" ]; then
    for candidate in /opt/swf-monitor/current/.venv/bin/python \
                     /data/wenauseic/github/swf-testbed/.venv/bin/python \
                     python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            PYBIN="${candidate}"
            break
        fi
    done
fi
"${PYBIN}" -m pip install --quiet --target "${WORK}/kit" "${REPO}"

PRMON=""
for candidate in "${REPO}/.prmon/bin/prmon" "${REPO}/.prmon/prmon"; do
    [ -x "${candidate}" ] && PRMON="${candidate}" && break
done
if [ -z "${PRMON}" ]; then
    bash "${REPO}/scripts/fetch_prmon.sh"
    for candidate in "${REPO}/.prmon/bin/prmon" "${REPO}/.prmon/prmon"; do
        [ -x "${candidate}" ] && PRMON="${candidate}" && break
    done
fi
if [ -z "${PRMON}" ]; then
    echo "prmon binary not found after fetch" >&2
    exit 1
fi
cp "${PRMON}" "${WORK}/kit/prmon"
chmod +x "${WORK}/kit/prmon"

tar -C "${WORK}" -czf "${WORK}/canary-kit.tgz" kit
echo "sandbox: ${WORK}/canary-kit.tgz ($(du -h "${WORK}/canary-kit.tgz" | cut -f1))"
