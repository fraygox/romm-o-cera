#!/bin/bash
# Build dolphin-tool for Batocera in a container. Commit/version = Batocera 43.1's
# dolphin-emu.mk (package/batocera/emulators/dolphin-emu, tag batocera-43.1).
set -euo pipefail
cd "$(dirname "$0")"
COMMIT=5e7cc91d8c9a43ca189b288937f65c9763af9c22
VERSION=2603a
ARCH=${1:-x86_64}                      # x86_64 | aarch64
mkdir -p out src
if [ "$ARCH" = aarch64 ]; then
    docker build -q -f Dockerfile.aarch64 -t local/dolphin-tool-build:bookworm-aarch64 . >/dev/null
    IMG=local/dolphin-tool-build:bookworm-aarch64
else
    docker build -q -t local/dolphin-tool-build:bookworm . >/dev/null
    IMG=local/dolphin-tool-build:bookworm
fi
docker run --rm -e DOLPHIN_COMMIT=$COMMIT -e DOLPHIN_VERSION=$VERSION -e ARCH=$ARCH \
    -v "$PWD/src:/src" -v "$PWD/out:/out" "$IMG"
