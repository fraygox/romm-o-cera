#!/usr/bin/env bash
# Build the user-facing release: dist/romm-o-cera.zip (only what runs on the
# box, in a rommbrowser/ folder) + dist/install.sh. No tests, tools or screenshots.
#   tools/build-release.sh            -> prints the version it built
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$HERE/dist}
VER=$(cd "$HERE" && python3 -c 'import rommlib; print(rommlib.VERSION)')
rm -rf "$OUT"; mkdir -p "$OUT/stage/rommbrowser"
for f in rommbrowser.pygame rommbrowser.pygame.keys rommlib.py rb_finalize.py rb_chd.py \
         rb_chd_plan.py rb_dl.py icon.png LICENSE; do
    cp "$HERE/$f" "$OUT/stage/rommbrowser/"
done
mkdir -p "$OUT/stage/rommbrowser/fonts"
cp "$HERE"/fonts/*.ttf "$HERE"/fonts/OFL-Rubik.txt "$OUT/stage/rommbrowser/fonts/"
cat > "$OUT/stage/rommbrowser/INSTALL.txt" <<TXT
romm-o-cera v$VER — RomM Browser for Batocera

1. Copy this rommbrowser folder to your Batocera box at
     \\\\BATOCERA\\share\\roms\\pygame\\rommbrowser     (SMB share)
   or  /userdata/roms/pygame/rommbrowser               (SSH / USB)
2. On the box: Main Menu → Game Settings → Update Gamelists.
3. Open Pygame → RomM Browser → Settings: enter your RomM URL and a client
   API token (RomM → Profile → Client API tokens; scopes me.read roms.read
   platforms.read assets.read collections.read firmware.read roms.user.read).

Hotkey + Start exits. Docs: https://github.com/fraygox/romm-o-cera
TXT
(cd "$OUT/stage" && find rommbrowser -type f | sort | TZ=UTC zip -q -X -D "$OUT/romm-o-cera.zip" -@)
cp "$HERE/tools/install.sh" "$OUT/install.sh"
rm -rf "$OUT/stage" "$HERE/__pycache__"
(cd "$OUT" && sha256sum romm-o-cera.zip install.sh > SHA256SUMS)
echo "$VER"
