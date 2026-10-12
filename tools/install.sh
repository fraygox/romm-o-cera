#!/bin/sh
# romm-o-cera installer — run ON the Batocera box (SSH, or Main Menu → … → a terminal):
#   curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
# Downloads the latest release, installs it to /userdata/roms/pygame/rommbrowser/
# (config, queue and logs in /userdata/system/romm-browser/ are kept), and
# tells you how to finish. Set REPO=owner/name to install from a fork,
# VERSION=nightly for the newest build of main (the nightly channel),
# VERSION=v0.3.0 to pin a release.
set -eu
REPO=${REPO:-fraygox/romm-o-cera}
VERSION=${VERSION:-latest}
DEST=${ROMM_O_CERA_DEST:-/userdata/roms/pygame/rommbrowser}      # (overrides: for tests)
DATA=${ROMM_O_CERA_DATA:-/userdata/system/romm-browser}
TMP=$(mktemp -d)
# an interrupted update or install (power cut mid-swap) can leave no app folder:
# put the last good copy back first, so the box has an app even if the download fails
if [ ! -d "$DEST" ]; then
    for old in "$DEST.old" "$DATA/app-backup.swap" "$DATA/app-backup"; do
        if [ -f "$old/rommbrowser.pygame" ]; then
            mkdir -p "$(dirname "$DEST")" && mv "$old" "$DEST" && echo "Restored the previous copy from $old"
            break
        fi
    done
fi
trap 'rm -rf "$TMP"' EXIT
if [ -n "${ROMM_O_CERA_BASE:-}" ]; then          # testing: install from another place
    BASE="$ROMM_O_CERA_BASE"
elif [ "$VERSION" = latest ]; then
    BASE="https://github.com/$REPO/releases/latest/download"
else
    BASE="https://github.com/$REPO/releases/download/$VERSION"
fi
echo "Downloading $BASE/romm-o-cera.zip"
curl -fsSL "$BASE/romm-o-cera.zip" -o "$TMP/romm-o-cera.zip"
curl -fsSL "$BASE/SHA256SUMS" -o "$TMP/SHA256SUMS"
# the release's checksum list must vouch for the zip (same check as the in-app updater)
(cd "$TMP" && grep " \*\?romm-o-cera.zip$" SHA256SUMS | sha256sum -c -) >/dev/null \
    || { echo "checksum mismatch: not installing"; exit 1; }
python3 - "$TMP/romm-o-cera.zip" "$TMP/x" <<'PY'
import sys, zipfile, os
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():                      # refuse anything escaping the folder
    if n.startswith("/") or ".." in n.split("/"):
        raise SystemExit("unsafe path in release zip: " + n)
z.extractall(sys.argv[2])
PY
[ -f "$TMP/x/rommbrowser/rommbrowser.pygame" ] || { echo "unexpected release layout"; exit 1; }
mkdir -p "$(dirname "$DEST")" "$DATA"
# a clean folder (files dropped from a release don't linger), swapped in at the end:
# one atomic exchange where the filesystem allows it (never a moment without an app)
rm -rf "$DEST.new" "$DEST.old"
cp -r "$TMP/x/rommbrowser" "$DEST.new"
if [ -d "$DEST" ] && python3 -c '
import ctypes, os, sys
libc = ctypes.CDLL("libc.so.6", use_errno=True)
sys.exit(0 if libc.renameat2(-100, os.fsencode(sys.argv[1]), -100, os.fsencode(sys.argv[2]), 2) == 0 else 1)
' "$DEST.new" "$DEST"; then
    rm -rf "$DEST.new"                        # holds the previous version now
else
    [ -d "$DEST" ] && mv "$DEST" "$DEST.old"
    mv "$DEST.new" "$DEST" || { [ -d "$DEST.old" ] && mv "$DEST.old" "$DEST"; echo "install failed"; exit 1; }
    rm -rf "$DEST.old"
fi
echo "Installed $(python3 -c "import sys;sys.path.insert(0,'$DEST');import rommlib;print('romm-o-cera v'+rommlib.VERSION)") to $DEST"
echo "Next: Main Menu → Game Settings → Update Gamelists, then Pygame → RomM-o-Cera → Settings"
echo "      to enter your RomM URL and client API token."
