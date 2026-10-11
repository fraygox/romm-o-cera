#!/bin/bash
# Package out/x86_64 (built by ./make.sh) for the GitHub release RomM-o-Cera downloads:
#   out/xbox360-tools-<ver>-x86_64.tar.gz   one top-level dir: extract-xiso, zarchive,
#                                           LICENSES/ (exact upstream texts), SOURCE.txt, README.txt
#   out/xbox360-tools-<ver>-src.tar.xz      the exact sources built (extract-xiso, ZArchive,
#                                           zstd) + this build recipe
#   out/SHA256SUMS
# Both archives are reproducible from the same src/ checkout and out/x86_64 files
# (fixed mtimes, sorted, numeric root owner, gzip -n).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/out"
B=x86_64
VER=$(sed -n 's/^xbox360-tools \([^ ]*\) .*/\1/p' "$B/BUILDINFO")
commit() { sed -n "s#^$1: https://github.com/[^ ]*/tree/\([0-9a-f]*\).*#\1#p" "$B/BUILDINFO"; }
EX=$(commit extract-xiso); ZA=$(commit zarchive); ZS=$(commit zstd)
ZSTAG=$(sed -n 's/^zstd: .*(\(v[0-9.]*\))$/\1/p' "$B/BUILDINFO")
[ -n "$VER" ] && [ -n "$EX" ] && [ -n "$ZA" ] && [ -n "$ZS" ] || { echo "bad BUILDINFO" >&2; exit 1; }
for d in extract-xiso:$EX ZArchive:$ZA zstd:$ZS; do        # src/ must still be what was built
    [ "$(git -C "$HERE/src/${d%%:*}" rev-parse HEAD)" = "${d#*:}" ] || { echo "src/${d%%:*} moved" >&2; exit 1; }
done
EPOCH=$(git -C "$HERE/src/extract-xiso" log -1 --format=%ct "$EX")   # newest of the three pins
TAR=(tar --sort=name --mtime="@$EPOCH" --owner=0 --group=0 --numeric-owner)
NAME=xbox360-tools-$VER
SRCNAME=$NAME-src.tar.xz

# ---- source archive
rm -rf srcpkg && mkdir -p "srcpkg/$NAME-src/recipe"
git -C "$HERE/src/extract-xiso" archive "$EX" | tar -x -C "srcpkg/$NAME-src" --one-top-level=extract-xiso
git -C "$HERE/src/ZArchive"     archive "$ZA" | tar -x -C "srcpkg/$NAME-src" --one-top-level=ZArchive
git -C "$HERE/src/zstd"         archive "$ZS" | tar -x -C "srcpkg/$NAME-src" --one-top-level=zstd
cp "$HERE"/{Dockerfile,make.sh,build.sh,package.sh} "srcpkg/$NAME-src/recipe/"
cat > "srcpkg/$NAME-src/README.txt" <<TXT
Corresponding source for xbox360-tools $VER (RomM-o-Cera's optional Xbox 360
download), exactly as built:

  extract-xiso/  https://github.com/XboxDev/extract-xiso  commit $EX
  ZArchive/      https://github.com/Exzap/ZArchive        commit $ZA
  zstd/          https://github.com/facebook/zstd         commit $ZS ($ZSTAG)
  recipe/        Dockerfile, make.sh, build.sh, package.sh (tools/xbox360-tools/
                 in https://github.com/fraygox/romm-o-cera)

Each directory is "git archive" of that commit, unmodified. To rebuild: put the
recipe files in a folder and run ./make.sh then ./package.sh (needs Docker; it
clones the same commits).
TXT
"${TAR[@]}" -C srcpkg -cf - "$NAME-src" | xz -9e -T1 > "$SRCNAME"

# ---- binary archive
rm -rf pkg && mkdir -p "pkg/$NAME/LICENSES"
cp "$B/extract-xiso" "$B/zarchive" "pkg/$NAME/"
chmod 755 "pkg/$NAME/extract-xiso" "pkg/$NAME/zarchive"
cp "$B/LICENSE-extract-xiso.txt" "$B/LICENSE-ZArchive.txt" "$B/LICENSE-zstd.txt" "$B/COPYING-zstd.txt" \
   "pkg/$NAME/LICENSES/"
cat > "pkg/$NAME/README.txt" <<TXT
xbox360-tools $VER for Batocera x86_64

Two command-line tools RomM-o-Cera (https://github.com/fraygox/romm-o-cera)
downloads when you turn on converting Xbox 360 disc images to .zar, the
compressed archive format xenia-canary opens directly:

  extract-xiso  extracts the game (XDVDFS) partition of an Xbox / Xbox 360
                disc image into a folder
  zarchive      packs a folder into a .zar archive (zstd-compressed) and
                unpacks one again

Both are unmodified upstream code; see SOURCE.txt for exactly what was built
and LICENSES/ for their licenses. Needs glibc 2.36 or newer (Batocera 43.1 has 2.40).

This product includes software developed by in <in@fishtank.com>.
TXT
cat > "pkg/$NAME/SOURCE.txt" <<TXT
xbox360-tools $VER for Batocera x86_64, shipped as an optional download by
RomM-o-Cera (https://github.com/fraygox/romm-o-cera).

extract-xiso
  upstream: https://github.com/XboxDev/extract-xiso
  commit:   $EX
            (CI release tag build-202609111233; reports itself as v2.7.1)
  license:  LICENSES/LICENSE-extract-xiso.txt (upstream LICENSE.TXT): a BSD-style
            license with an advertising clause, Copyright (c) 2003 in
            <in@fishtank.com> (SPDX: LicenseRef-extract-xiso, close to BSD-4-Clause)

zarchive (the ZArchive command-line tool, src/main.cpp)
  upstream: https://github.com/Exzap/ZArchive
  commit:   $ZA
            (the commit xenia-canary pins as third_party/zarchive, including the
            xenia-canary build Batocera 43.1 ships, f85f4c3)
  license:  LICENSES/LICENSE-ZArchive.txt (upstream LICENSE), MIT No Attribution,
            Copyright 2022 Exzap (SPDX: MIT-0). Its src/sha_256.c is derived from
            https://github.com/amosnier/sha-2 (Unlicense OR 0BSD).
  links in: zstd (below), statically; libstdc++ and libgcc statically (GCC
            runtime, GPL-3.0-or-later WITH GCC-exception-3.1, which places no
            conditions on the program).

zstd (inside zarchive)
  upstream: https://github.com/facebook/zstd
  commit:   $ZS ($ZSTAG)
  license:  dual BSD-3-Clause OR GPL-2.0-only; distributed here under BSD-3-Clause.
            LICENSES/LICENSE-zstd.txt (BSD) and LICENSES/COPYING-zstd.txt (GPLv2),
            Copyright (c) Meta Platforms, Inc. and affiliates.

No code was changed. Corresponding source, exactly as built (each project as
"git archive" of the commit above, plus the build recipe):

  $SRCNAME, attached to the same release

Build recipe: tools/xbox360-tools/ in the RomM-o-Cera repository (Dockerfile,
make.sh, build.sh, package.sh). Debian 12 container; CMake Release builds;
zstd as a static library with ZSTD_LEGACY_SUPPORT=OFF; glibc stays dynamic.

$(cat "$B/BUILDINFO")
TXT
"${TAR[@]}" -C pkg -cf - "$NAME" | gzip -n -9 > "$NAME-$B.tar.gz"
sha256sum "$NAME-$B.tar.gz" "$SRCNAME" | tee SHA256SUMS
ls -la "$NAME-$B.tar.gz" "$SRCNAME"
