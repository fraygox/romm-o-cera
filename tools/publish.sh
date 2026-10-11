#!/usr/bin/env bash
# For the maintainer: publish this folder from a parent monorepo to its own repo.
# Replays every parent commit that touched the folder as a commit whose root is
# the folder (what `git subtree split` does, without needing git-subtree).
# Author/committer/dates/messages are copied, so the replay is deterministic:
# re-running yields the same SHAs and the push fast-forwards.
#   tools/publish.sh <remote-url> [branch]      (or PUBLISH_REMOTE=...)
#
# --snapshot: for a public repo with its own (clean) history — adds ONE commit
# holding the folder's current committed state on top of the remote branch,
# instead of replaying the monorepo history. Message from -m or $PUBLISH_MSG;
# author from PUBLISH_AUTHOR_NAME / PUBLISH_AUTHOR_EMAIL, else git config
# publish.authorName / publish.authorEmail (set once in the monorepo so its own
# user.name never leaks into the public history), else user.name / user.email.
#   tools/publish.sh --snapshot -m "v1.2.1: ..." https://github.com/you/romm-o-cera.git
set -euo pipefail
SNAP=0; MSG=${PUBLISH_MSG:-}
while [ $# -gt 0 ]; do
    case "$1" in
        --snapshot) SNAP=1; shift ;;
        -m) MSG=$2; shift 2 ;;
        *) break ;;
    esac
done
REMOTE=${1:-${PUBLISH_REMOTE:-}}
BRANCH=${2:-main}
[ -n "$REMOTE" ] || { echo "usage: tools/publish.sh [--snapshot -m msg] <remote-url> [branch]" >&2; exit 1; }
HERE=$(cd "$(dirname "$0")/.." && pwd)
TOP=$(git -C "$HERE" rev-parse --show-toplevel)
P=${HERE#"$TOP"/}
cd "$TOP"
if [ "$SNAP" = 1 ]; then
    [ -n "$MSG" ] || { echo "--snapshot needs -m <message>" >&2; exit 1; }
    git fetch -q "$REMOTE" "$BRANCH"
    BASE=$(git rev-parse FETCH_HEAD)
    TREE=$(git rev-parse "HEAD:$P")
    [ "$(git rev-parse "$BASE^{tree}")" != "$TREE" ] || { echo "remote already has this tree"; exit 0; }
    N=${PUBLISH_AUTHOR_NAME:-$(git config publish.authorName || git config user.name)}
    E=${PUBLISH_AUTHOR_EMAIL:-$(git config publish.authorEmail || git config user.email)}
    C=$(printf '%s\n' "$MSG" | GIT_AUTHOR_NAME="$N" GIT_AUTHOR_EMAIL="$E" GIT_COMMITTER_NAME="$N" \
        GIT_COMMITTER_EMAIL="$E" git commit-tree "$TREE" -p "$BASE")
    git log --format='%h %an <%ae> | %s' -2 "$C"
    git push "$REMOTE" "$C:refs/heads/$BRANCH"
    exit 0
fi
PARENT=""
for c in $(git rev-list --reverse HEAD -- "$P"); do
    f() { git log -1 --format="$1" "$c"; }
    PARENT=$(f %B | GIT_AUTHOR_NAME="$(f %an)" GIT_AUTHOR_EMAIL="$(f %ae)" GIT_AUTHOR_DATE="$(f %aI)" \
        GIT_COMMITTER_NAME="$(f %cn)" GIT_COMMITTER_EMAIL="$(f %ce)" GIT_COMMITTER_DATE="$(f %cI)" \
        git commit-tree "$(git rev-parse "$c:$P")" ${PARENT:+-p "$PARENT"})
done
[ -n "$PARENT" ] || { echo "no commits touch $P" >&2; exit 1; }
git log --oneline -3 "$PARENT"
git push "$REMOTE" "$PARENT:refs/heads/$BRANCH"
