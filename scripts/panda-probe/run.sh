#!/bin/bash
# Submit the first canary probe task (PLAN.md increment 8): a small
# dedicated task of landing-kit jobs against a named queue. Each job
# fingerprints its node, runs the prmon-wrapped sample payload, and
# emits its landing report to stdout between CANARY-REPORT markers,
# collectable from the job logs. The probe carries no PanDA machinery
# beyond the sandbox; the payload is the committed landing kit.
#
# Usage: bash run.sh [spec.json]
# Requires the cached panda-client OIDC token (~/pclient/run/setup.sh)
# and the swf-monitor tree for the submit script.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SPEC="${1:-${HERE}/spec.json}"
STAMP="$(date +%Y%m%d%H%M)"
WORK="${SWF_TMP_DIR:-/data/swf-tmp}/canary-probe/${STAMP}"
mkdir -p "${WORK}/sandbox"

bash "${HERE}/build-sandbox.sh" "${WORK}/sandbox"
# The uploaded sandbox is extracted into the job workdir; kit/ arrives
# directly, so the inner tarball would only double the upload.
rm -f "${WORK}/sandbox/canary-kit.tgz"
sed "s/%STAMP%/${STAMP}/" "${SPEC}" > "${WORK}/spec.json"
echo "spec: ${WORK}/spec.json"
grep outDS "${WORK}/spec.json"

source ~/pclient/run/setup.sh
export PANDA_AUTH_VO=EIC.production
python3 /data/wenauseic/github/swf-monitor/scripts/evgen_panda_submit.py \
    --spec "${WORK}/spec.json" --workdir "${WORK}/sandbox"
