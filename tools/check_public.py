#!/usr/bin/env python3
"""Keep private details out of the public repo. Every commit is public as
soon as it's pushed, so the git hooks (tools/hooks/, enabled by
`tools/release.py setup`) run this before each commit and push:

  check_public.py --staged        the lines a commit adds
  check_public.py --msg FILE      a commit message
  check_public.py --push          pre-push: every commit about to be pushed
                                  (author, committer, message, added lines)
  check_public.py --range A..B    the same, for a range of commits

What counts as private is set per clone, in git config, never in the repo
(the list itself would give away what it hides):
  git config --add public.deny '<regex>'     case-insensitive, any number
  git config public.email '<address>'        the only author/committer allowed
"""
import re
import subprocess
import sys

ZERO = "0" * 40


def git(*args, check=True):
    r = subprocess.run(["git", *args], capture_output=True, text=True, errors="replace")
    if check and r.returncode:
        sys.exit("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def patterns():
    pats = [p for p in git("config", "--get-all", "public.deny", check=False).splitlines() if p.strip()]
    if not pats:
        sys.exit("check_public: no public.deny patterns in git config (see tools/check_public.py)")
    return [re.compile(p, re.I) for p in pats]


def hits(text, pats, where):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for p in pats:
            if p.search(line):
                out.append("%s:%d: matches /%s/" % (where, n, p.pattern))
    return out


def added_lines(diff):
    return "\n".join(ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))


def check_commit(sha, pats, email):
    bad = []
    who = git("log", "-1", "--format=%ae%n%ce", sha).split()
    if email and any(w != email for w in who):
        bad.append("%s: author/committer %s, not public.email" % (sha[:9], ", ".join(sorted(set(who)))))
    bad += hits(git("log", "-1", "--format=%an%n%cn%n%B", sha), pats, sha[:9] + " message")
    bad += hits(added_lines(git("show", "--format=", "--no-color", "--no-ext-diff", sha)), pats, sha[:9] + " diff")
    return bad


def main(argv):
    pats = patterns()
    email = git("config", "public.email", check=False).strip()
    bad = []
    if argv[:1] == ["--staged"]:
        bad = hits(added_lines(git("diff", "--cached", "--no-color", "--no-ext-diff")), pats, "staged")
        me = git("config", "user.email", check=False).strip()
        if email and me != email:
            bad.append("user.email is %s, not public.email: commits would carry it" % me)
    elif argv[:1] == ["--msg"]:
        with open(argv[1], encoding="utf-8", errors="replace") as f:
            msg = "\n".join(ln for ln in f.read().splitlines() if not ln.startswith("#"))
        bad = hits(msg, pats, "message")
    elif argv[:1] in (["--push"], ["--range"]):
        ranges = []
        if argv[0] == "--range":
            ranges.append(argv[1])
        else:
            for line in sys.stdin:
                parts = line.split()
                if len(parts) == 4 and parts[1] != ZERO:
                    ranges.append(parts[1] if parts[3] == ZERO else "%s..%s" % (parts[3], parts[1]))
        for rg in ranges:
            extra = ["--not", "--remotes"] if ".." not in rg else []
            for sha in git("rev-list", rg, *extra).split():
                bad += check_commit(sha, pats, email)
    else:
        sys.exit(__doc__)
    if bad:
        print("Not public-safe:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
