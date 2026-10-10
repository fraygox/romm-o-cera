#!/bin/sh
# romm-o-cera installer — run ON the Batocera box (SSH, or Main Menu → … → a terminal):
#   curl -fsSL https://github.com/fraygox/romm-o-cera/releases/latest/download/install.sh | sh
# Downloads the latest release, installs it to /userdata/roms/pygame/rommbrowser/
# (config, queue and logs in /userdata/system/romm-browser/ are kept), and
# tells you how to finish. Set REPO=owner/name to install from a fork,
# VERSION=v0.3.0 to pin a release.
set -eu
REPO=${REPO:-fraygox/romm-o-cera}
VERSION=${VERSION:-latest}
DEST=/userdata/roms/pygame/rommbrowser
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
if [ "$VERSION" = latest ]; then
    URL="https://github.com/$REPO/releases/latest/download/romm-o-cera.zip"
else
    URL="https://github.com/$REPO/releases/download/$VERSION/romm-o-cera.zip"
fi
echo "Downloading $URL"
curl -fsSL "$URL" -o "$TMP/r.zip"
python3 - "$TMP/r.zip" "$TMP/x" <<'PY'
import sys, zipfile, os
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():                      # refuse anything escaping the folder
    if n.startswith("/") or ".." in n.split("/"):
        raise SystemExit("unsafe path in release zip: " + n)
z.extractall(sys.argv[2])
PY
[ -f "$TMP/x/rommbrowser/rommbrowser.pygame" ] || { echo "unexpected release layout"; exit 1; }
mkdir -p "$DEST" /userdata/system/romm-browser
cp -rf "$TMP"/x/rommbrowser/. "$DEST"/
rm -rf "$DEST/__pycache__"
echo "Installed $(python3 -c "import sys;sys.path.insert(0,'$DEST');import rommlib;print('romm-o-cera v'+rommlib.VERSION)") to $DEST"
echo "Next: Main Menu → Game Settings → Update Gamelists, then Pygame → RomM Browser → Settings"
echo "      to enter your RomM URL and client API token."
