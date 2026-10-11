#!/bin/bash
# Usage (from this folder):  ./make.sh   ->  out/dolphin-tool, out/BUILDINFO
set -euo pipefail
COMMIT=${DOLPHIN_COMMIT:?}          # same commit Batocera 43.1 builds (dolphin-emu.mk)
VERSION=${DOLPHIN_VERSION:?}
ARCH=${ARCH:-x86_64}
CROSS=()
TOOLP=""
if [ "$ARCH" = aarch64 ]; then
    TOOLP=aarch64-linux-gnu-
    export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
    CROSS=(-DCMAKE_SYSTEM_NAME=Linux -DCMAKE_SYSTEM_PROCESSOR=aarch64
           -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc -DCMAKE_CXX_COMPILER=aarch64-linux-gnu-g++
           -DCMAKE_LIBRARY_ARCHITECTURE=aarch64-linux-gnu
           -DCMAKE_FIND_ROOT_PATH="/usr/aarch64-linux-gnu;/" -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER
           -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH
           -DLIBUDEV_LIBRARY=/usr/lib/aarch64-linux-gnu/libudev.so -DLIBUDEV_INCLUDE_DIR=/usr/include
           -DOPENGL_opengl_LIBRARY=/usr/lib/aarch64-linux-gnu/libOpenGL.so
           -DOPENGL_glx_LIBRARY=/usr/lib/aarch64-linux-gnu/libGLX.so
           -DOPENGL_gl_LIBRARY=/usr/lib/aarch64-linux-gnu/libGL.so)
fi
OUT=/out/$ARCH
mkdir -p "$OUT"
cd /src
if [ ! -d dolphin/.git ]; then
    git clone --filter=blob:none https://github.com/dolphin-emu/dolphin.git
fi
cd dolphin
git fetch -q origin "$COMMIT" || true
git checkout -q -f "$COMMIT"
git submodule update --init --recursive --depth 1 -q
sed -i "s/set(DOLPHIN_VERSION_MAJOR .*)/set(DOLPHIN_VERSION_MAJOR \"$VERSION\")/" CMake/ScmRevGen.cmake 2>/dev/null || true
cmake -S . -B /build/$ARCH -G Ninja "${CROSS[@]}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_CLI_TOOL=ON -DENABLE_QT=OFF -DENABLE_NOGUI=OFF \
    -DENABLE_X11=OFF -DENABLE_EGL=OFF -DENABLE_VULKAN=OFF -DENABLE_LLVM=OFF \
    -DENABLE_ALSA=OFF -DENABLE_PULSEAUDIO=OFF -DENABLE_EVDEV=OFF -DENABLE_SDL=OFF \
    -DENABLE_TESTS=OFF -DENABLE_AUTOUPDATE=OFF -DENABLE_ANALYTICS=OFF \
    -DUSE_DISCORD_PRESENCE=OFF -DUSE_MGBA=OFF -DUSE_UPNP=OFF -DUSE_RETRO_ACHIEVEMENTS=OFF \
    -DENCODE_FRAMEDUMPS=OFF -DENABLE_HEADLESS=ON \
    -DUSE_SYSTEM_LIBS=OFF -DDISTRIBUTOR='romm-o-cera' \
    -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" > "$OUT/cmake.log" 2>&1 \
    || { tail -40 "$OUT/cmake.log"; exit 1; }
ninja -C /build/$ARCH dolphin-tool > "$OUT/ninja.log" 2>&1 || { tail -60 "$OUT/ninja.log"; exit 1; }
BIN=$(find /build/$ARCH -name dolphin-tool -type f -perm -u+x | head -1)
cp "$BIN" "$OUT/dolphin-tool"
${TOOLP}strip "$OUT/dolphin-tool"
cp COPYING "$OUT/COPYING-dolphin" 2>/dev/null || cp license.txt "$OUT/COPYING-dolphin"
cp -r LICENSES "$OUT/LICENSES" 2>/dev/null || true
{
  echo "dolphin-tool $VERSION ($COMMIT) built for Batocera $ARCH"
  echo "source: https://github.com/dolphin-emu/dolphin/tree/$COMMIT"
  echo "built: $(date -u +%FT%TZ) on $(. /etc/os-release; echo $PRETTY_NAME), $(${TOOLP}gcc --version | head -1)"
  echo "needs: $(${TOOLP}objdump -T "$OUT/dolphin-tool" | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1)"
  echo "libs: $(${TOOLP}objdump -p "$OUT/dolphin-tool" | awk '/NEEDED/{print $2}' | tr '\n' ' ')"
} > "$OUT/BUILDINFO"
cat "$OUT/BUILDINFO"
if [ "$ARCH" = aarch64 ]; then         # smoke test under emulation
    qemu-aarch64-static -L /usr/aarch64-linux-gnu "$OUT/dolphin-tool" --help 2>&1 | tail -1 || true
fi
