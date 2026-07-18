#!/bin/bash
# Fetch the prmon static binary release into .prmon/ (gitignored).
# Usage: scripts/fetch_prmon.sh [version]   (default: latest release)
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH=$(uname -m)
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    VERSION=$(curl -sf https://api.github.com/repos/HSF/prmon/releases/latest \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
fi
VERSION="${VERSION#v}"

TARBALL="prmon_${VERSION}_${ARCH}-static-gnu115-opt.tar.gz"
URL="https://github.com/HSF/prmon/releases/download/v${VERSION}/${TARBALL}"

mkdir -p .prmon
echo "fetching $URL"
curl -sfL "$URL" -o ".prmon/$TARBALL"
tar -xzf ".prmon/$TARBALL" -C .prmon --strip-components=1
rm ".prmon/$TARBALL"
# Sanity check: the binary runs (prmon has no --version option).
.prmon/bin/prmon --help > /dev/null
echo "prmon $VERSION installed at .prmon/bin/prmon"
