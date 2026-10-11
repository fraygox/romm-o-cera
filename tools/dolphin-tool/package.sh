#!/bin/bash
# Package out/dolphin-tool for the GitHub release romm-o-cera downloads:
#   out/dolphin-tool-<ver>-x86_64.tar.gz  (+ .sha256)
# Contents: dolphin-tool, COPYING (Dolphin's GPL-2.0-or-later), LICENSES/ (the
# licenses of code Dolphin bundles), SOURCE.txt (exact source + how it was built).
set -euo pipefail
ARCH=${1:-x86_64}                  # x86_64 | aarch64 (built by ./make.sh <arch> into out/<arch>/)
cd "$(dirname "$0")/out"
VER=$(sed -n 's/^dolphin-tool \([^ ]*\) .*/\1/p' "$ARCH/BUILDINFO")
COMMIT=$(sed -n 's/^dolphin-tool [^ ]* (\([0-9a-f]*\)).*/\1/p' "$ARCH/BUILDINFO")
rm -rf pkg && mkdir -p pkg
cp "$ARCH/dolphin-tool" pkg/
cp "$ARCH/COPYING-dolphin" pkg/COPYING
cp -r "$ARCH/LICENSES" pkg/LICENSES
cat > pkg/SOURCE.txt <<TXT
dolphin-tool $VER for Batocera $ARCH, shipped as an optional download by
RomM-o-Cera (https://github.com/fraygox/romm-o-cera) to convert GameCube and
Wii disc images to RVZ.

dolphin-tool is part of the Dolphin emulator (https://dolphin-emu.org),
licensed GPL-2.0-or-later (see COPYING; code Dolphin bundles: LICENSES/).
Its code is unmodified; the only change is the build-time version label
(CMake/ScmRevGen.cmake: DOLPHIN_VERSION_MAJOR "$VER"), the same edit Batocera
makes. Corresponding source, exactly as built (with submodules):

  dolphin-$VER-${COMMIT:0:8}-source.tar.xz, attached to the same release
  upstream: https://github.com/dolphin-emu/dolphin/tree/$COMMIT

This is the commit Batocera 43.1 builds Dolphin from (batocera.linux,
tag batocera-43.1, package/batocera/emulators/dolphin-emu/dolphin-emu.mk),
built with ENABLE_CLI_TOOL=ON. Build recipe: tools/dolphin-tool/ in the
RomM-o-Cera repository (Dockerfile, build.sh, make.sh).

$(cat "$ARCH/BUILDINFO")
TXT
NAME=dolphin-tool-$VER-$ARCH.tar.gz
tar -C pkg --owner=0 --group=0 --sort=name -czf "$NAME" .
sha256sum "$NAME" | tee "$NAME.sha256"
ls -la "$NAME"
