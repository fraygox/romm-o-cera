#!/usr/bin/env bash
# Deploy RomM Browser to a Batocera box over SSH. Swaps the whole app folder
# in one step, as the in-app updater does — never reloads EmulationStation and
# never starts or kills anything on the box.
#
#   ROMM_URL=https://romm.example.com ROMM_TOKEN=rmm_... ./deploy.sh <batocera-host>
#
# Variables (all optional except the host):
#   ROMM_URL     RomM base URL (http:// or https://). Unset = keep the box's current one.
#   ROMM_TOKEN   RomM client API token. Unset = keep the box's current one.
#   ROMM_VERIFY_TLS  "false" for a self-signed cert, or a CA-bundle path on the box.
#   SSH_KEY      private key for root@host (default: your ssh config / agent;
#                .env: ROMM_BATOCERA_SSH_KEY)
#   SSH_USER     default root
#   FORCE=1      deploy even while a game (or RomM-o-Cera) is running on the box
# Both can also be set later on the box: Pygame → RomM Browser → Settings.
#
# A .env next to this script (git-ignored; or ROMM_O_CERA_ENV=path) may hold
# ROMM_BATOCERA_{HOST,URL,TOKEN,SSH_KEY} as fallbacks.
#
# How: the release's file set (tools/app-files.sh) is copied to a local temp
# folder and BUILD is stamped there, uploaded to DATA/app-staging/rommbrowser,
# compile-checked on the box and swapped in by tools/deploy_swap.py (atomic
# renameat2 exchange, else two renames). The previous app becomes
# DATA/app-backup, so the app's "Go back" row works. A failed check leaves the
# live app untouched. Nothing else in DATA (settings, logs, tools, caches) is touched.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ENVF="${ROMM_O_CERA_ENV:-$HERE/.env}"
envget() { [ -f "$ENVF" ] && sed -n "s/^$1=//p" "$ENVF" | head -1 || true; }
HOST=${1:-$(envget ROMM_BATOCERA_HOST)}
[ -n "$HOST" ] || { echo "usage: ./deploy.sh <batocera-host>" >&2; exit 1; }
URL=${ROMM_URL:-$(envget ROMM_BATOCERA_URL)}
TOKEN=${ROMM_TOKEN:-${ROMM_BROWSER_TOKEN:-$(envget ROMM_BATOCERA_TOKEN)}}
VERIFY=${ROMM_VERIFY_TLS:-}
SSH_OPTS=(-o ConnectTimeout=5)
KEYF=${SSH_KEY:-$(envget ROMM_BATOCERA_SSH_KEY)}
KEYF=${KEYF/#\~/$HOME}
[ -n "$KEYF" ] && SSH_OPTS+=(-i "$KEYF")
TARGET="${SSH_USER:-root}@$HOST"
APPDIR=${ROMM_O_CERA_DEST:-/userdata/roms/pygame/rommbrowser}     # (overrides: for tests)
DATA=${ROMM_O_CERA_DATA:-/userdata/system/romm-browser}
# never swap files under someone playing (or using the app): ES says what runs
if [ -z "${FORCE:-}" ]; then
    RUNNING=$(ssh "${SSH_OPTS[@]}" "$TARGET" "curl -s -m 3 localhost:1234/runningGame" || true)
    case "$RUNNING" in
        ""|*"NO GAME RUNNING"*) ;;
        *) echo "Something is running on $HOST, not deploying under it (FORCE=1 to anyway): $RUNNING" >&2; exit 1 ;;
    esac
fi
# a staging copy of exactly what the release zip ships, stamped with the git
# commit as an internal build (the repo keeps BUILD="")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
# shellcheck source=tools/app-files.sh
. "$HERE/tools/app-files.sh"
copy_app_files "$HERE" "$TMP/rommbrowser"
BUILD_ID=$(cd "$HERE" && git rev-parse --short HEAD 2>/dev/null || true)
if [ -n "$BUILD_ID" ]; then
    (cd "$HERE" && git diff --quiet -- . 2>/dev/null) || BUILD_ID="$BUILD_ID-dirty"
    sed -i.orig "s/^BUILD = \"\"/BUILD = \"$BUILD_ID\"/" "$TMP/rommbrowser/rommlib.py"
    rm -f "$TMP/rommbrowser/rommlib.py.orig"
    grep -q "^BUILD = \"$BUILD_ID\"" "$TMP/rommbrowser/rommlib.py" || { echo "couldn't stamp BUILD" >&2; exit 1; }
fi
# upload it beside the app's data (same filesystem as the app: one-step swap),
# then check and swap on the box; only app-staging and app-backup are touched
ssh "${SSH_OPTS[@]}" "$TARGET" "rm -rf '$DATA/app-staging' && mkdir -p '$DATA/app-staging'"
scp -rq "${SSH_OPTS[@]}" "$TMP/rommbrowser" "$TARGET:$DATA/app-staging/"
ssh "${SSH_OPTS[@]}" "$TARGET" "python3 - '$DATA' '$APPDIR'" < "$HERE/tools/deploy_swap.py"
# config.json: merge — only keys given here are changed. Values travel via the
# environment of the local helper and ssh's stdin — never as command-line
# arguments, where `ps` on either machine could show the token.
RB_URL="$URL" RB_TOKEN="$TOKEN" RB_VERIFY="$VERIFY" python3 -c 'import json,os; print(json.dumps({k: os.environ[e] for k, e in (("url","RB_URL"),("token","RB_TOKEN"),("verify_tls","RB_VERIFY")) if os.environ.get(e)}))' \
    | ssh "${SSH_OPTS[@]}" "$TARGET" "umask 077; python3 -c '
import json, os, sys
p = os.path.join(sys.argv[1], \"config.json\")
new = json.load(sys.stdin)
if \"verify_tls\" in new:
    v = new[\"verify_tls\"].lower()
    new[\"verify_tls\"] = False if v in (\"0\", \"false\", \"no\") else True if v in (\"1\", \"true\", \"yes\") else new[\"verify_tls\"]
cfg = json.load(open(p)) if os.path.exists(p) else {}
cfg.update(new)
fd = os.open(p + \".tmp\", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, \"w\") as f:
    json.dump(cfg, f, indent=1)
os.replace(p + \".tmp\", p)
print(\"config: url=%s token=%s\" % (cfg.get(\"url\") or \"(unset)\", \"set\" if cfg.get(\"token\") else \"(unset)\"))
' '$DATA'"
echo "Deployed to $HOST:$APPDIR"
echo "First install: in ES run Main Menu → Game Settings → Update Gamelists, then Pygame → RomM-o-Cera."
