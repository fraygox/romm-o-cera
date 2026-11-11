#!/bin/bash
# Build extract-xiso + zarchive for Batocera in a container, then package:
#   ./make.sh [x86_64|aarch64]     ->  out/<arch>/{extract-xiso,zarchive,BUILDINFO,...}
#   ./package.sh [x86_64|aarch64]  ->  out/xbox360-tools-<ver>-<arch>.tar.gz, -src.tar.xz, SHA256SUMS
# x86_64: Batocera's xenia and xenia-canary (under Wine). aarch64 (cross-built, plain ARMv8,
# checked under qemu-user): xenia-edge, the native port Batocera 44 adds on Snapdragon boards.
#
# Pins:
#  - ZArchive: the commit xenia-canary pins as third_party/zarchive at the
#    xenia-canary build Batocera 43.1 ships (f85f4c3), so the .zar files we
#    write are read by exactly the same reader code. xenia-edge (Batocera 44's
#    build, e987fd7) pins the same commit.
#  - extract-xiso: XboxDev/extract-xiso CI release build-202609111233 (its
#    version string is still 2.7.1; the old "v2.7.1" git tag is from 2014 and
#    predates XGD1 fixes and the empty-directory fixes).
#  - zstd: latest upstream release, statically linked into zarchive.
set -euo pipefail
cd "$(dirname "$0")"
VERSION=2611a
ZARCHIVE_COMMIT=b467f7aa342895e66a1a003377ed63b3c1be3de3
EXTRACT_XISO_COMMIT=3f5b62cfe68f000b0e3c8a30104973f3a297948e
ZSTD_TAG=v1.5.7
ZSTD_COMMIT=f8745da6ff1ad1e7bab384bd1f9d742439278e99
ARCH=${1:-x86_64}
mkdir -p out src
if [ "$ARCH" = aarch64 ]; then
    IMG=local/xbox360-tools-build:bookworm-aarch64
    docker build -q -f Dockerfile.aarch64 -t $IMG . >/dev/null
else
    IMG=local/xbox360-tools-build:bookworm
    docker build -q -t $IMG . >/dev/null
fi
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e ARCH="$ARCH" \
    -e VERSION=$VERSION -e ZARCHIVE_COMMIT=$ZARCHIVE_COMMIT \
    -e EXTRACT_XISO_COMMIT=$EXTRACT_XISO_COMMIT -e ZSTD_TAG=$ZSTD_TAG -e ZSTD_COMMIT=$ZSTD_COMMIT \
    -v "$PWD/src:/src" -v "$PWD/out:/out" $IMG
