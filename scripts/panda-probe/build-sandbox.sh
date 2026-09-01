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
python3 -m pip install --quiet --target "${WORK}/kit" "${REPO}"

if [ ! -x "${REPO}/.prmon/prmon" ]; then
    bash "${REPO}/scripts/fetch_prmon.sh"
fi
cp "${REPO}/.prmon/prmon" "${WORK}/kit/prmon"
chmod +x "${WORK}/kit/prmon"

tar -C "${WORK}" -czf "${WORK}/canary-kit.tgz" kit
echo "sandbox: ${WORK}/canary-kit.tgz ($(du -h "${WORK}/canary-kit.tgz" | cut -f1))"
