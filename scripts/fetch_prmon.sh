#!/bin/bash
# Fetch the prmon static binary into the repository's .prmon/ (gitignored).
# The fetch itself ships in the package (canary/probe_kit/fetch_prmon.sh),
# where the probe sandbox build uses it; this is the development entry.
# Usage: scripts/fetch_prmon.sh [version]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec bash "${HERE}/../canary/probe_kit/fetch_prmon.sh" "${HERE}/../.prmon" "$@"
