#!/bin/bash
# Build dolphin-tool for Batocera in a container. Commit/version = a tagged Dolphin
# release (never master). It needn't be the commit Batocera's own Dolphin is built from:
# the RVZ format is the same, and the app converts and verifies with this one binary.
set -euo pipefail
cd "$(dirname "$0")"
COMMIT=3a5b3743b18da3e8f04cbc44f20dc50fbd3a9ef3
VERSION=2609b
ARCH=${1:-x86_64}                      # x86_64 | aarch64
mkdir -p out src
if [ "$ARCH" = aarch64 ]; then
    docker build -q -f Dockerfile.aarch64 -t local/dolphin-tool-build:bookworm-aarch64 . >/dev/null
    IMG=local/dolphin-tool-build:bookworm-aarch64
else
    docker build -q -t local/dolphin-tool-build:bookworm . >/dev/null
    IMG=local/dolphin-tool-build:bookworm
fi
# (the build runs as root in the container: git must accept a checkout the host user owns,
# and what it writes is handed back to that user afterwards)
docker run --rm -e DOLPHIN_COMMIT=$COMMIT -e DOLPHIN_VERSION=$VERSION -e ARCH=$ARCH \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e 'GIT_CONFIG_VALUE_0=*' \
    -v "$PWD/src:/src" -v "$PWD/out:/out" "$IMG"
docker run --rm -v "$PWD/src:/src" -v "$PWD/out:/out" --entrypoint chown "$IMG" -R "$(id -u):$(id -g)" /src /out
