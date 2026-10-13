#!/bin/bash
# Build wuatool for Batocera in a container, then package:
#   ./make.sh [x86_64|aarch64]  ->  out/<arch>/{wuatool,BUILDINFO,LICENSE-*,...} (self-tested)
#   ./package.sh                ->  out/wiiu-tools-<ver>-<arch>.tar.gz, -src.tar.xz, SHA256SUMS
# aarch64 is cross-compiled on the x86_64 host (plain armv8-a) and self-tested under qemu.
#
# Pins:
#  - Cemu: its disc code (FST, WUD, ncrypto, AES), MPL-2.0, unmodified but for
#    one added line (patch-cemu.py) that lets the self-test swap in a made-up
#    common key, and one added condition that lets wuatool open a disc's other
#    game partitions (update, DLC). The Wii U common key wuatool uses is Cemu's own, compiled in
#    from this commit's src/Cemu/ncrypto/ncrypto.cpp.
#  - ZArchive: the commit this Cemu pins as dependencies/ZArchive (build.sh
#    checks), so .wua files are written by the code Cemu's own "Convert to .wua"
#    uses and read back by the reader Cemu opens them with.
#  - zstd: latest upstream release, statically linked (as in xbox360-tools).
set -euo pipefail
cd "$(dirname "$0")"
VERSION=2610b
CEMU_COMMIT=b9b46ecb65e2eb1c15793380048309df73d2084e
ZARCHIVE_COMMIT=d2c717730092c7bf8cbb033b12fd4001b7c4d932
ZSTD_TAG=v1.5.7
ZSTD_COMMIT=f8745da6ff1ad1e7bab384bd1f9d742439278e99
ARCH=${1:-x86_64}
mkdir -p out src
if [ "$ARCH" = aarch64 ]; then
    IMG=local/wiiu-tools-build:bookworm-aarch64
    docker build -q -f Dockerfile.aarch64 -t $IMG . >/dev/null
else
    IMG=local/wiiu-tools-build:bookworm
    docker build -q -t $IMG . >/dev/null
fi
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e ARCH="$ARCH" \
    -e VERSION=$VERSION -e CEMU_COMMIT=$CEMU_COMMIT -e ZARCHIVE_COMMIT=$ZARCHIVE_COMMIT \
    -e ZSTD_TAG=$ZSTD_TAG -e ZSTD_COMMIT=$ZSTD_COMMIT \
    -v "$PWD:/recipe:ro" -v "$PWD/src:/src" -v "$PWD/out:/out" $IMG
