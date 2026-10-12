#!/usr/bin/env bash
# Deploy RomM Browser to a Batocera box over SSH. Copies files only — never
# reloads EmulationStation and never starts or kills anything on the box.
#
#   ROMM_URL=https://romm.example.com ROMM_TOKEN=rmm_... ./deploy.sh <batocera-host>
#
# Variables (all optional except the host):
#   ROMM_URL     RomM base URL (http:// or https://). Unset = keep the box's current one.
#   ROMM_TOKEN   RomM client API token. Unset = keep the box's current one.
#   ROMM_VERIFY_TLS  "false" for a self-signed cert, or a CA-bundle path on the box.
#   SSH_KEY      private key for root@host (default: your ssh config / agent;
#                monorepo .env: ROMM_BATOCERA_SSH_KEY)
#   SSH_USER     default root
# Both can also be set later on the box: Pygame → RomM Browser → Settings.
#
# Monorepo convenience: if this folder lives two levels under a directory with
# a .env, ROMM_BATOCERA_{URL,TOKEN,HOST} there are used as fallbacks.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ENVF="$HERE/../../.env"
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
APPDIR=/userdata/roms/pygame/rommbrowser
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p $APPDIR/fonts $APPDIR/romm /userdata/system/romm-browser"
# romm/ before rommlib.py: a process started mid-deploy never sees a front without its package
scp -q "${SSH_OPTS[@]}" "$HERE"/romm/*.py "$TARGET:$APPDIR/romm/"
scp -q "${SSH_OPTS[@]}" "$HERE"/rommbrowser.pygame "$HERE"/rommbrowser.pygame.keys "$HERE"/rommlib.py \
    "$HERE"/rb_finalize.py "$HERE"/rb_chd.py "$HERE"/rb_chd_plan.py "$HERE"/rb_dl.py "$HERE"/rb_repair.py "$HERE"/icon.png "$TARGET:$APPDIR/"
scp -q "${SSH_OPTS[@]}" "$HERE"/fonts/*.ttf "$HERE"/fonts/OFL-Rubik.txt "$TARGET:$APPDIR/fonts/"
# config.json: merge — only keys given here are changed. Values travel via the
# environment of the local helper and ssh's stdin — never as command-line
# arguments, where `ps` on either machine could show the token.
RB_URL="$URL" RB_TOKEN="$TOKEN" RB_VERIFY="$VERIFY" python3 -c 'import json,os; print(json.dumps({k: os.environ[e] for k, e in (("url","RB_URL"),("token","RB_TOKEN"),("verify_tls","RB_VERIFY")) if os.environ.get(e)}))' \
    | ssh "${SSH_OPTS[@]}" "$TARGET" "umask 077; python3 -c '
import json, os, sys
p = \"/userdata/system/romm-browser/config.json\"
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
'"
# internal build: stamp the deployed copy with the git commit (the repo keeps BUILD="")
BUILD_ID=$(cd "$HERE" && git rev-parse --short HEAD 2>/dev/null || true)
if [ -n "$BUILD_ID" ]; then
    (cd "$HERE" && git diff --quiet -- . 2>/dev/null) || BUILD_ID="$BUILD_ID-dirty"
    ssh "${SSH_OPTS[@]}" "$TARGET" "sed -i 's/^BUILD = \"\"/BUILD = \"$BUILD_ID\"/' $APPDIR/rommlib.py"
fi
ssh "${SSH_OPTS[@]}" "$TARGET" "cd $APPDIR && python3 -m py_compile rommlib.py romm/*.py rb_finalize.py rb_chd.py rb_chd_plan.py rb_dl.py rb_repair.py && python3 -c 'import ast;ast.parse(open(\"rommbrowser.pygame\").read())' && rm -rf __pycache__ romm/__pycache__"
echo "Deployed to $HOST:$APPDIR"
echo "First install: in ES run Main Menu → Game Settings → Update Gamelists, then Pygame → RomM-o-Cera."
