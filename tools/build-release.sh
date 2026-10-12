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
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac      # it's used after a cd
# read from the file, not by importing it: a build machine needs no app dependencies
lit() { sed -n "s/^$1 = \"\([^\"]*\)\".*/\1/p" "$HERE/rommlib.py" | head -1; }
VER=$(lit VERSION); REL=$(lit RELEASE_DATE); BLD=$(lit BUILD)
[ -n "$VER" ] || { echo "no VERSION in rommlib.py" >&2; exit 1; }
[ -z "$BLD" ] || { echo "rommlib.BUILD must be empty in a release (is '$BLD')" >&2; exit 1; }
NIGHTLY=${NIGHTLY:-}
if [ -n "$NIGHTLY" ]; then
    [[ "$NIGHTLY" =~ ^n[0-9]{8}\.[0-9]{4}\.[0-9a-f]{7,40}$ ]] || { echo "NIGHTLY must look like n20261012.1903.ab12cd3" >&2; exit 1; }
elif [ "$REL" != "$(date +%F)" ] && [ -z "${ALLOW_OLD_DATE:-}" ]; then
    echo "rommlib.RELEASE_DATE is $REL, not today ($(date +%F)): bump it (or ALLOW_OLD_DATE=1)" >&2
    exit 1
fi
rm -rf "$OUT"; mkdir -p "$OUT/stage"
# shellcheck source=tools/app-files.sh
. "$HERE/tools/app-files.sh"            # the same file set deploy.sh ships
copy_app_files "$HERE" "$OUT/stage/rommbrowser"
if [ -n "$NIGHTLY" ]; then
    sed -i "s/^BUILD = \"\" /BUILD = \"$NIGHTLY\" /" "$OUT/stage/rommbrowser/rommlib.py"
    grep -q "^BUILD = \"$NIGHTLY\"" "$OUT/stage/rommbrowser/rommlib.py" || { echo "couldn't stamp BUILD" >&2; exit 1; }
    VER="$VER+$NIGHTLY"
fi
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
