#!/bin/bash
# Runs inside the container (see make.sh). Clones the pinned sources into /src,
# builds in /tmp/build, writes wuatool + license files + BUILDINFO to /out/$ARCH,
# then runs the self-test (synthetic discs, random keys; under qemu for aarch64).
set -euo pipefail
: "${VERSION:?}" "${CEMU_COMMIT:?}" "${ZARCHIVE_COMMIT:?}" "${ZSTD_TAG:?}" "${ZSTD_COMMIT:?}"
ARCH=${ARCH:-x86_64}
OUT=/out/$ARCH
B=/tmp/build/$ARCH
TOOLP="" CROSS=() MARCH="" RUN=() TRIPLET=x86_64-linux-gnu
if [ "$ARCH" = aarch64 ]; then
    TOOLP=aarch64-linux-gnu- TRIPLET=aarch64-linux-gnu
    MARCH="-march=armv8-a"            # plain ARMv8: Pi 4 (Cortex-A72) and every newer ARM box
    CROSS=(-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64
           -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++)
    RUN=(qemu-aarch64-static -L /usr/aarch64-linux-gnu)
fi
CC=${TOOLP}gcc CXX=${TOOLP}g++
FLAGS="-O2 $MARCH -fstack-protector-strong -D_FORTIFY_SOURCE=2 -ffile-prefix-map=/src=. -ffile-prefix-map=$B=."
rm -rf "$OUT" "$B" && mkdir -p "$OUT" "$B"

fetch() {   # fetch <dir> <url> <commit>
    [ -d "/src/$1/.git" ] || git clone -q --filter=blob:none --no-checkout "$2" "/src/$1"
    git -C "/src/$1" cat-file -e "$3^{commit}" 2>/dev/null || git -C "/src/$1" fetch -q origin "$3"
    git -C "/src/$1" checkout -q -f "$3"
    git -C "/src/$1" clean -qfdx
    [ "$(git -C "/src/$1" rev-parse HEAD)" = "$3" ] || { echo "$1: not at $3" >&2; exit 1; }
}
fetch zstd     https://github.com/facebook/zstd.git  "$ZSTD_COMMIT"
fetch ZArchive https://github.com/Exzap/ZArchive.git "$ZARCHIVE_COMMIT"
fetch Cemu     https://github.com/cemu-project/Cemu.git "$CEMU_COMMIT"
# the ZArchive pinned here must be the one this Cemu pins (the reader Cemu opens .wua files with)
[ "$(git -C /src/Cemu ls-tree HEAD dependencies/ZArchive | awk '{print $3}')" = "$ZARCHIVE_COMMIT" ] \
    || { echo "ZArchive pin differs from Cemu's dependencies/ZArchive" >&2; exit 1; }

# Cemu's src/ (a copy: /src stays exactly upstream), with the test-only override added
mkdir -p "$B/cemu"
git -C /src/Cemu archive HEAD src | tar -x -C "$B/cemu"
python3 /recipe/patch-cemu.py "$B/cemu/src"

# zstd: static library only
cmake -S /src/zstd/build/cmake -B "$B/zstd" "${CROSS[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="$FLAGS" -DCMAKE_INSTALL_PREFIX="$B/zstd-install" \
    -DZSTD_BUILD_STATIC=ON -DZSTD_BUILD_SHARED=OFF -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_TESTS=OFF -DZSTD_LEGACY_SUPPORT=OFF > "$OUT/zstd-cmake.log" 2>&1 \
    || { tail -40 "$OUT/zstd-cmake.log"; exit 1; }
cmake --build "$B/zstd" -j"$(nproc)" --target install > "$OUT/zstd-build.log" 2>&1 \
    || { tail -40 "$OUT/zstd-build.log"; exit 1; }
ZLIB_A=$(find "$B/zstd-install" -name libzstd.a | head -1)

# wuatool: Cemu's disc code (FST, WUD, ncrypto, AES) + ZArchive + our two files
C=$B/cemu/src Z=/src/ZArchive
CXXFLAGS="$FLAGS -std=c++20 -include Common/precompiled.h -I$C -I$Z/include -I$B/zstd-install/include \
 -include $B/wuatool_version.h -DFMT_HEADER_ONLY -DBOOST_ALL_NO_LIB -w"
SRCS=(/recipe/wuatool.cpp /recipe/cemu_stubs.cpp "$C/Cafe/Filesystem/FST/FST.cpp" "$C/Cafe/Filesystem/WUD/wud.cpp"
      "$C/Cemu/ncrypto/ncrypto.cpp" "$C/util/crypto/aes128.cpp" "$C/Common/unix/FileStream_unix.cpp"
      "$C/Common/cpu_features.cpp")
mkdir -p "$B/obj"
echo "#define WUATOOL_VERSION \"$VERSION\"" > "$B/wuatool_version.h"
printf '%s\n' "${SRCS[@]}" | xargs -P"$(nproc)" -I{} sh -c \
    "$CXX $CXXFLAGS -c '{}' -o '$B/obj/'\$(basename '{}').o" > "$OUT/wuatool-build.log" 2>&1 \
    || { tail -60 "$OUT/wuatool-build.log"; exit 1; }
$CXX -O2 $MARCH -std=c++20 -I$Z/include -I$B/zstd-install/include -c $Z/src/zarchivewriter.cpp -o "$B/obj/zw.o"
$CXX -O2 $MARCH -std=c++20 -I$Z/include -I$B/zstd-install/include -c $Z/src/zarchivereader.cpp -o "$B/obj/zr.o"
$CC -O2 $MARCH -c $Z/src/sha_256.c -o "$B/obj/sha.o"
LIBCRYPTO=/usr/lib/$TRIPLET/libcrypto.a
$CXX "$B"/obj/*.o -o "$OUT/wuatool" -static-libstdc++ -static-libgcc "$ZLIB_A" "$LIBCRYPTO" \
    -lpthread -ldl -Wl,-z,relro,-z,now >> "$OUT/wuatool-build.log" 2>&1 \
    || { tail -60 "$OUT/wuatool-build.log"; exit 1; }
${TOOLP}strip "$OUT/wuatool"

# exact license texts of everything compiled in
cp /src/Cemu/LICENSE.txt "$OUT/LICENSE-Cemu.txt"
cp /src/ZArchive/LICENSE "$OUT/LICENSE-ZArchive.txt"
cp /src/zstd/LICENSE     "$OUT/LICENSE-zstd.txt"
cp /src/zstd/COPYING     "$OUT/COPYING-zstd.txt"
OSSL_PKG=$(dpkg-query -W -f='${Version}' "libssl-dev${TOOLP:+:arm64}")
cp /usr/share/doc/libssl-dev/copyright "$OUT/COPYRIGHT-OpenSSL.txt"
cp /usr/share/doc/libfmt-dev/copyright "$OUT/COPYRIGHT-fmt.txt"
cp /usr/share/doc/libglm-dev/copyright "$OUT/COPYRIGHT-glm.txt"
cp /usr/share/doc/libx11-dev/copyright "$OUT/COPYRIGHT-Xlib.txt"
cp "$(ls -d /usr/share/doc/libboost1.*-dev | head -1)/copyright" "$OUT/COPYRIGHT-Boost.txt"
need() { ${TOOLP}objdump -T "$1" | grep -o 'GLIBC_[0-9.]*' | sort -Vu | tail -1; }
libs() { ${TOOLP}objdump -p "$1" | awk '/NEEDED/{print $2}' | tr '\n' ' '; }
{
  echo "wiiu-tools $VERSION built for Batocera $ARCH"
  echo "cemu: https://github.com/cemu-project/Cemu/tree/$CEMU_COMMIT"
  echo "zarchive: https://github.com/Exzap/ZArchive/tree/$ZARCHIVE_COMMIT"
  echo "zstd: https://github.com/facebook/zstd/tree/$ZSTD_COMMIT ($ZSTD_TAG)"
  echo "openssl: Debian 12 libssl-dev $OSSL_PKG (static libcrypto)"
  echo "built: $(date -u +%FT%TZ) on $(. /etc/os-release; echo "$PRETTY_NAME"), $($CXX --version | head -1)"
  echo "flags: $FLAGS -std=c++20; LDFLAGS=-static-libstdc++ -static-libgcc -Wl,-z,relro,-z,now; stripped"
  echo "wuatool needs: $(need "$OUT/wuatool"), libs: $(libs "$OUT/wuatool")"
} > "$OUT/BUILDINFO"
cat "$OUT/BUILDINFO"
"${RUN[@]}" "$OUT/wuatool" --version
python3 /recipe/selftest.py "${RUN[@]}" "$OUT/wuatool"
