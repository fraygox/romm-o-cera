#!/bin/bash
# Runs inside the container (see make.sh). Clones the pinned sources into /src,
# builds into /tmp/build, writes binaries + license files + BUILDINFO to /out/$ARCH
# (x86_64, or aarch64 cross-built for plain ARMv8 and run under qemu-user for the checks).
set -euo pipefail
: "${VERSION:?}" "${ZARCHIVE_COMMIT:?}" "${EXTRACT_XISO_COMMIT:?}" "${ZSTD_TAG:?}" "${ZSTD_COMMIT:?}"
ARCH=${ARCH:-x86_64}
OUT=/out/$ARCH
TOOLP="" CROSS=() MARCH="" RUN=()
if [ "$ARCH" = aarch64 ]; then
    TOOLP=aarch64-linux-gnu-
    MARCH=" -march=armv8-a"           # plain ARMv8: every 64-bit ARM box
    CROSS=(-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64
           -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++)
    RUN=(qemu-aarch64-static -L /usr/aarch64-linux-gnu)
fi
CFLAGS_COMMON="-O2$MARCH -fstack-protector-strong -D_FORTIFY_SOURCE=2 -ffile-prefix-map=/src=."
rm -rf "$OUT" /tmp/build && mkdir -p "$OUT" /tmp/build

fetch() {   # fetch <dir> <url> <commit>
    [ -d "/src/$1/.git" ] || git clone -q "$2" "/src/$1"
    git -C "/src/$1" cat-file -e "$3^{commit}" 2>/dev/null || git -C "/src/$1" fetch -q origin "$3"
    git -C "/src/$1" checkout -q -f "$3"
    git -C "/src/$1" clean -qfdx
    [ "$(git -C "/src/$1" rev-parse HEAD)" = "$3" ] || { echo "$1: not at $3" >&2; exit 1; }
}
fetch zstd         https://github.com/facebook/zstd.git         "$ZSTD_COMMIT"
fetch ZArchive     https://github.com/Exzap/ZArchive.git        "$ZARCHIVE_COMMIT"
fetch extract-xiso https://github.com/XboxDev/extract-xiso.git  "$EXTRACT_XISO_COMMIT"

# zstd: static library only (multithreading support compiled in, as upstream's default)
cmake -S /src/zstd/build/cmake -B /tmp/build/zstd "${CROSS[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="$CFLAGS_COMMON" -DCMAKE_INSTALL_PREFIX=/tmp/build/zstd-install \
    -DZSTD_BUILD_STATIC=ON -DZSTD_BUILD_SHARED=OFF -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_TESTS=OFF -DZSTD_LEGACY_SUPPORT=OFF > "$OUT/zstd-cmake.log" 2>&1 \
    || { tail -40 "$OUT/zstd-cmake.log"; exit 1; }
cmake --build /tmp/build/zstd -j"$(nproc)" --target install > "$OUT/zstd-build.log" 2>&1 \
    || { tail -40 "$OUT/zstd-build.log"; exit 1; }

# ZArchive: upstream's BUILD_STATIC_TOOL would link glibc statically too; we
# keep glibc dynamic and link zstd, libstdc++ and libgcc statically instead.
cmake -S /src/ZArchive -B /tmp/build/ZArchive "${CROSS[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH=/tmp/build/zstd-install -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_C_FLAGS="$CFLAGS_COMMON" -DCMAKE_CXX_FLAGS="$CFLAGS_COMMON" \
    -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now" \
    > "$OUT/zarchive-cmake.log" 2>&1 || { tail -40 "$OUT/zarchive-cmake.log"; exit 1; }
cmake --build /tmp/build/ZArchive -j"$(nproc)" --target zarchiveTool > "$OUT/zarchive-build.log" 2>&1 \
    || { tail -40 "$OUT/zarchive-build.log"; exit 1; }

# extract-xiso: plain C, glibc only
cmake -S /src/extract-xiso -B /tmp/build/extract-xiso "${CROSS[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="$CFLAGS_COMMON" -DCMAKE_EXE_LINKER_FLAGS="-Wl,-z,relro,-z,now" \
    > "$OUT/extract-xiso-cmake.log" 2>&1 || { tail -40 "$OUT/extract-xiso-cmake.log"; exit 1; }
cmake --build /tmp/build/extract-xiso -j"$(nproc)" > "$OUT/extract-xiso-build.log" 2>&1 \
    || { tail -40 "$OUT/extract-xiso-build.log"; exit 1; }

cp /tmp/build/ZArchive/zarchive /tmp/build/extract-xiso/extract-xiso "$OUT/"
${TOOLP}strip "$OUT/zarchive" "$OUT/extract-xiso"
# exact upstream license texts
cp /src/extract-xiso/LICENSE.TXT "$OUT/LICENSE-extract-xiso.txt"
cp /src/ZArchive/LICENSE         "$OUT/LICENSE-ZArchive.txt"
cp /src/zstd/LICENSE             "$OUT/LICENSE-zstd.txt"
cp /src/zstd/COPYING             "$OUT/COPYING-zstd.txt"
need() { ${TOOLP}objdump -T "$1" | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1; }
libs() { ${TOOLP}objdump -p "$1" | awk '/NEEDED/{print $2}' | tr '\n' ' '; }
{
  echo "xbox360-tools $VERSION built for Batocera $ARCH"
  echo "extract-xiso: https://github.com/XboxDev/extract-xiso/tree/$EXTRACT_XISO_COMMIT"
  echo "zarchive: https://github.com/Exzap/ZArchive/tree/$ZARCHIVE_COMMIT"
  echo "zstd: https://github.com/facebook/zstd/tree/$ZSTD_COMMIT ($ZSTD_TAG)"
  echo "built: $(date -u +%FT%TZ) on $(. /etc/os-release; echo "$PRETTY_NAME"), $(${TOOLP}gcc --version | head -1), $(cmake --version | head -1)"
  echo "flags: CFLAGS/CXXFLAGS=$CFLAGS_COMMON (CMake Release adds -O3 -DNDEBUG); zstd static (ZSTD_LEGACY_SUPPORT=OFF);"
  echo "       zarchive LDFLAGS=-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now; extract-xiso LDFLAGS=-Wl,-z,relro,-z,now; stripped"
  echo "extract-xiso needs: $(need "$OUT/extract-xiso"), libs: $(libs "$OUT/extract-xiso")"
  echo "zarchive needs: $(need "$OUT/zarchive"), libs: $(libs "$OUT/zarchive")"
} > "$OUT/BUILDINFO"
cat "$OUT/BUILDINFO"
"${RUN[@]}" "$OUT/extract-xiso" -v
"${RUN[@]}" "$OUT/zarchive" | head -1
# a round trip on made-up files: folder -> disc image -> folder -> .zar -> folder, all the same
T=/tmp/build/trip && rm -rf "$T" && mkdir -p "$T/in/sub" && cd "$T"
head -c 300000 /dev/urandom > in/default.xex && head -c 70000 /dev/zero > in/sub/blank.bin && echo hello > in/sub/a.txt
"${RUN[@]}" "$OUT/extract-xiso" -c in img.iso > /dev/null
"${RUN[@]}" "$OUT/extract-xiso" -x img.iso -d out1 > /dev/null
"${RUN[@]}" "$OUT/zarchive" out1 game.zar > /dev/null
"${RUN[@]}" "$OUT/zarchive" game.zar out2 > /dev/null
diff -r in out1 && diff -r in out2 && echo "round trip: ok ($(stat -c %s img.iso) byte image, $(stat -c %s game.zar) byte .zar)"
