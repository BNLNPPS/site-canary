#!/bin/bash
# Submit one canary probe task (PLAN.md increment 8) against a named
# queue: a single landing-kit job that fingerprints its node, runs the
# prmon-wrapped sample payload, and emits its landing report to stdout
# between CANARY-REPORT markers, collectable from the job logs.
#
# This is the probe submission doer: the canary agent's dispatch path
# runs it per due queue, and it remains directly runnable.
#
# Usage: bash run.sh [queue] [spec.json]
# Prints the submit script's output; success includes jediTaskID=<id>.
# Requires the cached panda-client OIDC token (~/pclient/run/setup.sh)
# and the swf-monitor tree for the submit script.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
QUEUE="${1:-BNL_OSG_EPIC_PROD_1}"
SPEC="${2:-${HERE}/spec.json}"
QTAG="$(echo "${QUEUE}" | tr 'A-Z' 'a-z')"
STAMP="$(date +%Y%m%d%H%M%S)"
WORK="${SWF_TMP_DIR:-/data/swf-tmp}/canary-probe/${STAMP}"
mkdir -p "${WORK}/sandbox"

bash "${HERE}/build-sandbox.sh" "${WORK}/sandbox" >&2
# The uploaded sandbox is extracted into the job workdir; kit/ arrives
# directly, so the inner tarball would only double the upload.
rm -f "${WORK}/sandbox/canary-kit.tgz"
sed -e "s/%STAMP%/${STAMP}/" -e "s/%QUEUE%/${QUEUE}/" \
    -e "s/%QTAG%/${QTAG}/" "${SPEC}" > "${WORK}/spec.json"
echo "spec: ${WORK}/spec.json" >&2
grep outDS "${WORK}/spec.json" >&2

source ~/pclient/run/setup.sh
export PANDA_AUTH_VO=EIC.production
python3 /data/wenauseic/github/swf-monitor/scripts/evgen_panda_submit.py \
    --spec "${WORK}/spec.json" --workdir "${WORK}/sandbox"
