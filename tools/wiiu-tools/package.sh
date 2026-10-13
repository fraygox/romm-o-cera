#!/bin/bash
# Package out/<arch>/ (built by ./make.sh <arch>) for the GitHub release RomM-o-Cera downloads:
#   out/wiiu-tools-<ver>-<arch>.tar.gz   one top-level dir: wuatool, LICENSES/, SOURCE.txt, README.txt
#   out/wiiu-tools-<ver>-src.tar.xz      the exact sources built (Cemu's src/, ZArchive, zstd) + this recipe
#   out/SHA256SUMS
# Packages every arch that's built. Re-packaging the same out/<arch> files gives the
# same archives (fixed mtimes, sorted, numeric root owner, gzip -n); a fresh build
# needn't (BUILDINFO's date, unpinned base image), so romm/tools.py pins the
# published archives, not a rebuild.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/out"
ARCHES=()
for a in x86_64 aarch64; do [ -f "$a/wuatool" ] && ARCHES+=("$a"); done
[ ${#ARCHES[@]} -gt 0 ] || { echo "nothing built (./make.sh first)" >&2; exit 1; }
B=${ARCHES[0]}
VER=$(sed -n 's/^wiiu-tools \([^ ]*\) .*/\1/p' "$B/BUILDINFO")
commit() { sed -n "s#^$1: https://github.com/[^ ]*/tree/\([0-9a-f]*\).*#\1#p" "$B/BUILDINFO"; }
CE=$(commit cemu); ZA=$(commit zarchive); ZS=$(commit zstd)
ZSTAG=$(sed -n 's/^zstd: .*(\(v[0-9.]*\))$/\1/p' "$B/BUILDINFO")
[ -n "$VER" ] && [ -n "$CE" ] && [ -n "$ZA" ] && [ -n "$ZS" ] || { echo "bad BUILDINFO" >&2; exit 1; }
for a in "${ARCHES[@]}"; do          # every arch built from the same pins
    [ "$(grep -v '^built:\|^wiiu-tools\|^flags:\|needs:' "$a/BUILDINFO")" = \
      "$(grep -v '^built:\|^wiiu-tools\|^flags:\|needs:' "$B/BUILDINFO")" ] || { echo "$a: other pins" >&2; exit 1; }
done
for d in Cemu:$CE ZArchive:$ZA zstd:$ZS; do        # src/ must still be what was built
    [ "$(git -C "$HERE/src/${d%%:*}" rev-parse HEAD)" = "${d#*:}" ] || { echo "src/${d%%:*} moved" >&2; exit 1; }
done
EPOCH=$(git -C "$HERE/src/Cemu" log -1 --format=%ct "$CE")
TAR=(tar --sort=name --mtime="@$EPOCH" --owner=0 --group=0 --numeric-owner)
NAME=wiiu-tools-$VER
SRCNAME=$NAME-src.tar.xz
RECIPE=(Dockerfile Dockerfile.aarch64 make.sh build.sh package.sh patch-cemu.py wuatool.cpp cemu_stubs.cpp
        mkdisc.py selftest.py)

# ---- source archive
rm -rf srcpkg && mkdir -p "srcpkg/$NAME-src/recipe"
git -C "$HERE/src/Cemu"     archive "$CE" src LICENSE.txt | tar -x -C "srcpkg/$NAME-src" --one-top-level=Cemu
git -C "$HERE/src/ZArchive" archive "$ZA" | tar -x -C "srcpkg/$NAME-src" --one-top-level=ZArchive
git -C "$HERE/src/zstd"     archive "$ZS" | tar -x -C "srcpkg/$NAME-src" --one-top-level=zstd
for f in "${RECIPE[@]}"; do cp "$HERE/$f" "srcpkg/$NAME-src/recipe/"; done
cat > "srcpkg/$NAME-src/README.txt" <<TXT
Corresponding source for wiiu-tools $VER (RomM-o-Cera's optional Wii U
download), exactly as built:

  Cemu/      https://github.com/cemu-project/Cemu     commit $CE
             (its src/ folder and LICENSE.txt; wuatool compiles a few of its files)
  ZArchive/  https://github.com/Exzap/ZArchive        commit $ZA
  zstd/      https://github.com/facebook/zstd         commit $ZS ($ZSTAG)
  recipe/    the build recipe and wuatool's own source (tools/wiiu-tools/ in
             https://github.com/fraygox/romm-o-cera): wuatool.cpp and
             cemu_stubs.cpp (MPL-2.0), patch-cemu.py (adds one line to
             Cemu's src/Cemu/ncrypto/ncrypto.cpp before it's compiled),
             Dockerfile, Dockerfile.aarch64, make.sh, build.sh, package.sh,
             mkdisc.py and selftest.py (the self-test)

Each upstream directory is "git archive" of that commit, unmodified. To rebuild:
put the recipe files in a folder and run ./make.sh x86_64 (or aarch64), then
./package.sh (needs Docker; it clones the same commits). OpenSSL's libcrypto
comes from Debian 12's libssl-dev package (see the BUILDINFO in SOURCE.txt).
TXT
"${TAR[@]}" -C srcpkg -cf - "$NAME-src" | xz -9e -T1 > "$SRCNAME"

# ---- binary archives
for A in "${ARCHES[@]}"; do
rm -rf pkg && mkdir -p "pkg/$NAME/LICENSES"
cp "$A/wuatool" "pkg/$NAME/"
chmod 755 "pkg/$NAME/wuatool"
cp "$A"/LICENSE-*.txt "$A"/COPYING-*.txt "$A"/COPYRIGHT-*.txt "pkg/$NAME/LICENSES/"
cat > "pkg/$NAME/README.txt" <<TXT
wiiu-tools $VER for Batocera $A

wuatool, a command-line tool RomM-o-Cera (https://github.com/fraygox/romm-o-cera)
downloads when you turn on Wii U to .wux or Decrypt Wii U discs.

wuatool wux compresses a Wii U disc image (.wud) into .wux, the lossless
container Cemu reads (each distinct 32 KB sector stored once); no keys are
needed, and wuatool unwux gives back the exact .wud. wuatool compare reads a
.wux through Cemu's own reader and checks it holds the .wud's bytes.

wuatool pack turns a disc image (.wud, .wux) into .wua, the compressed game
archive Cemu opens directly. It reads the disc's game partition with Cemu's
own disc code and writes it with ZArchive, the library Cemu's own "Convert to
.wua" uses; then it reads both back and compares them. A disc needs its disc
key (Cemu's keys.txt) for this. The Wii U common key is Cemu's, compiled in
from Cemu's source. No disc keys are included.

See SOURCE.txt for exactly what was built and LICENSES/ for the licenses.
Needs glibc 2.36 or newer (Batocera 43.1 has 2.40).
TXT
cat > "pkg/$NAME/SOURCE.txt" <<TXT
wiiu-tools $VER for Batocera $A, shipped as an optional download by
RomM-o-Cera (https://github.com/fraygox/romm-o-cera).

wuatool
  source:   wuatool.cpp and cemu_stubs.cpp (tools/wiiu-tools/ in the RomM-o-Cera
            repository), MPL-2.0. Its .wux writer is its own (Exzap's WudCompress,
            which introduced the format, carries no license), written to the
            layout Cemu's src/Cafe/Filesystem/WUD/wud.cpp reads.
  compiles: from Cemu (below) src/Cafe/Filesystem/FST/FST.cpp,
            src/Cafe/Filesystem/WUD/wud.cpp, src/Cemu/ncrypto/ncrypto.cpp,
            src/util/crypto/aes128.cpp, src/Common/unix/FileStream_unix.cpp and
            src/Common/cpu_features.cpp, with their headers. One line is added
            to ncrypto.cpp before it's compiled (patch-cemu.py): a call that lets
            wuatool's test option swap in a made-up common key.

Cemu
  upstream: https://github.com/cemu-project/Cemu
  commit:   $CE
  license:  LICENSES/LICENSE-Cemu.txt (upstream LICENSE.txt), Mozilla Public
            License 2.0 (SPDX: MPL-2.0)

ZArchive (the reader and writer, src/zarchivereader.cpp, zarchivewriter.cpp, sha_256.c)
  upstream: https://github.com/Exzap/ZArchive
  commit:   $ZA (the commit Cemu $CE pins as dependencies/ZArchive)
  license:  LICENSES/LICENSE-ZArchive.txt, MIT No Attribution, Copyright 2022
            Exzap (SPDX: MIT-0). src/sha_256.c is derived from
            https://github.com/amosnier/sha-2 (Unlicense OR 0BSD).

zstd (statically linked)
  upstream: https://github.com/facebook/zstd
  commit:   $ZS ($ZSTAG)
  license:  dual BSD-3-Clause OR GPL-2.0-only; distributed here under BSD-3-Clause.
            LICENSES/LICENSE-zstd.txt (BSD) and LICENSES/COPYING-zstd.txt (GPLv2),
            Copyright (c) Meta Platforms, Inc. and affiliates.

OpenSSL libcrypto (statically linked; Debian 12's libssl-dev)
  license:  Apache-2.0; LICENSES/COPYRIGHT-OpenSSL.txt (Debian's copyright file)

Headers compiled in: Boost (BSL-1.0), {fmt} (MIT), GLM (MIT), Xlib (MIT; included
by Cemu's platform.h, not linked): LICENSES/COPYRIGHT-*.txt. libstdc++ and libgcc
are linked statically (GPL-3.0-or-later WITH GCC-exception-3.1, which places no
conditions on the program).

Corresponding source, exactly as built (each project as "git archive" of the
commit above, plus wuatool's source and the build recipe):

  $SRCNAME, attached to the same release

$(cat "$A/BUILDINFO")
TXT
"${TAR[@]}" -C pkg -cf - "$NAME" | gzip -n -9 > "$NAME-$A.tar.gz"
done
( for A in "${ARCHES[@]}"; do sha256sum "$NAME-$A.tar.gz"; done; sha256sum "$SRCNAME" ) | tee SHA256SUMS
ls -la "$NAME"-*.tar.*
