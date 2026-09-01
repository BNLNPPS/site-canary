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
