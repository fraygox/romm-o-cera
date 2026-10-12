#!/usr/bin/env bash
# Build the user-facing release: dist/romm-o-cera.zip (only what runs on the
# box, in a rommbrowser/ folder) + dist/install.sh. No tests, tools or screenshots.
#   tools/build-release.sh            -> prints the version it built
# A nightly build: NIGHTLY=n<YYYYMMDD>.<HHMM>.<commit> (UTC) stamps that as
# BUILD in the packaged rommlib.py only (the app then shows 0.8.1+n…, and
# orders it after 0.8.1 and before 0.8.2); RELEASE_DATE isn't checked.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$HERE/dist}
# -B: no __pycache__ left in the repo (tests in docker leave root-owned ones there)
VER=$(cd "$HERE" && python3 -B -c 'import rommlib; print(rommlib.VERSION)')
REL=$(cd "$HERE" && python3 -B -c 'import rommlib; print(rommlib.RELEASE_DATE)')
BLD=$(cd "$HERE" && python3 -B -c 'import rommlib; print(rommlib.BUILD)')
[ -z "$BLD" ] || { echo "rommlib.BUILD must be empty in a release (is '$BLD')" >&2; exit 1; }
NIGHTLY=${NIGHTLY:-}
if [ -n "$NIGHTLY" ]; then
    [[ "$NIGHTLY" =~ ^n[0-9]{8}\.[0-9]{4}\.[0-9a-f]{7,40}$ ]] || { echo "NIGHTLY must look like n20261012.1903.ab12cd3" >&2; exit 1; }
elif [ "$REL" != "$(date +%F)" ] && [ -z "${ALLOW_OLD_DATE:-}" ]; then
    echo "rommlib.RELEASE_DATE is $REL, not today ($(date +%F)): bump it (or ALLOW_OLD_DATE=1)" >&2
    exit 1
fi
rm -rf "$OUT"; mkdir -p "$OUT/stage/rommbrowser"
for f in rommbrowser.pygame rommbrowser.pygame.keys rommlib.py rb_finalize.py rb_chd.py \
         rb_chd_plan.py rb_dl.py rb_repair.py icon.png LICENSE; do
    cp "$HERE/$f" "$OUT/stage/rommbrowser/"
done
if [ -n "$NIGHTLY" ]; then
    sed -i "s/^BUILD = \"\" /BUILD = \"$NIGHTLY\" /" "$OUT/stage/rommbrowser/rommlib.py"
    grep -q "^BUILD = \"$NIGHTLY\"" "$OUT/stage/rommbrowser/rommlib.py" || { echo "couldn't stamp BUILD" >&2; exit 1; }
    VER="$VER+$NIGHTLY"
fi
mkdir -p "$OUT/stage/rommbrowser/romm"
cp "$HERE"/romm/*.py "$OUT/stage/rommbrowser/romm/"
mkdir -p "$OUT/stage/rommbrowser/fonts"
cp "$HERE"/fonts/*.ttf "$HERE"/fonts/OFL-Rubik.txt "$OUT/stage/rommbrowser/fonts/"
cat > "$OUT/stage/rommbrowser/INSTALL.txt" <<TXT
RomM-o-Cera v$VER — a RomM client for Batocera, by fraygox

1. Copy this rommbrowser folder to your Batocera box at
     \\\\BATOCERA\\share\\roms\\pygame\\rommbrowser     (SMB share)
   or  /userdata/roms/pygame/rommbrowser               (SSH / USB)
2. On the box: Main Menu → Game Settings → Update Gamelists.
3. Open Pygame → RomM-o-Cera → Settings: enter your RomM URL and a client
   API token (RomM → Profile → Client API tokens; scopes me.read roms.read
   platforms.read assets.read collections.read firmware.read roms.user.read).

Hotkey + Start exits. Docs: https://github.com/fraygox/romm-o-cera
TXT
(cd "$OUT/stage" && find rommbrowser -type f | sort | TZ=UTC zip -q -X -D "$OUT/romm-o-cera.zip" -@)
cp "$HERE/tools/install.sh" "$OUT/install.sh"
rm -rf "$OUT/stage"
(cd "$OUT" && sha256sum romm-o-cera.zip install.sh > SHA256SUMS)
echo "$VER"
