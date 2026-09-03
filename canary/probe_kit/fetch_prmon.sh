#!/bin/bash
# Fetch the prmon static binary release into a directory: the
# repository's .prmon/ (gitignored) by default, or the directory given,
# which is how the probe sandbox build caches it under the shared tree.
# Usage: fetch_prmon.sh [dir] [version]   (version default: latest release)
set -euo pipefail
DIR="${1:-$(cd "$(dirname "$0")/../.." && pwd)/.prmon}"
VERSION="${2:-}"
ARCH=$(uname -m)
if [ -z "$VERSION" ]; then
    VERSION=$(curl -sf https://api.github.com/repos/HSF/prmon/releases/latest \
        | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
fi
VERSION="${VERSION#v}"

TARBALL="prmon_${VERSION}_${ARCH}-static-gnu115-opt.tar.gz"
URL="https://github.com/HSF/prmon/releases/download/v${VERSION}/${TARBALL}"

mkdir -p "$DIR"
echo "fetching $URL"
curl -sfL "$URL" -o "$DIR/$TARBALL"
tar -xzf "$DIR/$TARBALL" -C "$DIR" --strip-components=1
rm "$DIR/$TARBALL"
# Sanity check: the binary runs (prmon has no --version option).
"$DIR/bin/prmon" --help > /dev/null
echo "prmon $VERSION installed at $DIR/bin/prmon"
