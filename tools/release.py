#!/usr/bin/env python3
"""Ship RomM-o-Cera. Every commit goes to GitHub's main; GitHub Actions does
the building (tests.yml, nightly.yml, release.yml, docs.yml).

  tools/release.py setup                 once per clone: git hooks, checks the public identity
  tools/release.py push                  push new commits to main (checked by the pre-push hook)
  tools/release.py nightly [--wait]      push, then build the nightly now (pushes build it anyway)
  tools/release.py stable [options]      cut a stable release:
      --bump patch|minor|major           (default patch) or --version X.Y.Z
      --notes FILE                       the CHANGELOG section (Markdown, ### groups); default:
                                         written from the commits since the last release
      --summary TEXT                     one line for the release title (default: from the notes)
      --no-screenshots                   keep the README screenshots as they are
      --wait                             watch the release workflow until it's published
  tools/release.py screenshots           regenerate the README screenshots and commit them
  tools/release.py docs                  publish main's docs site now (else: each stable release)
  tools/release.py changelog             print the notes the next stable release would get
  tools/release.py notes vX.Y.Z | --nightly    (CI) a release's notes
  tools/release.py title vX.Y.Z          (CI) a release's title

Options for every command: --dry-run (say what would happen, change nothing).
Python with pygame for screenshots: ROMM_PY (default python3), e.g.
  ROMM_PY="docker run --rm -u $(id -u) -e HOME=/tmp -v $PWD:$PWD -w $PWD rb-test:local python"
Commits follow Conventional Commits (feat/fix/perf/security/refactor/docs/test/chore):
the type decides where a change lands in the notes.
"""
import argparse
import datetime
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "fraygox/romm-o-cera"
CHANGELOG = os.path.join(HERE, "CHANGELOG.md")
GROUPS = [("feat", "Features"), ("security", "Security"), ("perf", "Performance"), ("fix", "Fixes"),
          ("refactor", "Under the hood"), ("docs", "Documentation")]
INSTALL = ("Update on the box: **Settings → Updates**, or "
           "`curl -fsSL https://github.com/%s/releases/latest/download/install.sh | sh`" % REPO)
DRY = False


def say(msg):
    print("» " + msg, flush=True)


def run(*cmd, capture=False, check=True, change=True, env=None):
    """Run a command in the repo. change=True: skipped under --dry-run."""
    if DRY and change:
        say("would run: " + " ".join(shlex.quote(c) for c in cmd))
        return ""
    r = subprocess.run(cmd, cwd=HERE, text=True, capture_output=capture, env=env)
    if check and r.returncode:
        sys.exit("failed (%d): %s%s" % (r.returncode, " ".join(cmd), "\n" + (r.stderr or "") if capture else ""))
    return (r.stdout or "").strip() if capture else ""


def git(*args, change=False):
    return run("git", *args, capture=True, change=change)


def version():
    src = open(os.path.join(HERE, "rommlib.py"), encoding="utf-8").read()
    return re.search(r'^VERSION = "([^"]+)"', src, re.M).group(1)


def last_tag():
    tags = [t for t in git("tag", "--list", "v*", "--sort=-v:refname").split() if re.match(r"^v\d+\.\d+\.\d+$", t)]
    return tags[0] if tags else None


# ---------------------------------------------------------------- notes

def commits_since(tag):
    rng = "%s..HEAD" % tag if tag else "HEAD"
    out = git("log", "--reverse", "--no-merges", "--format=%H%x1f%B%x1e", rng)
    for rec in out.split("\x1e"):
        if "\x1f" in rec:
            sha, body = rec.strip().split("\x1f", 1)
            yield sha, body.strip()


def draft_notes(tag):
    """Markdown grouped by commit type, oldest first: '- **fix:** Subject.'
    (subjects are written for users; the body is for developers)."""
    by = {k: [] for k, _ in GROUPS}
    for _sha, body in commits_since(tag):
        subject = body.partition("\n")[0]
        m = re.match(r"^(\w+)(?:\([^)]*\))?(!)?:\s*(.+)$", subject)
        if not m or m.group(1) in ("chore", "test", "ci", "build", "style") or re.match(r"^v\d", m.group(3)):
            continue
        kind = m.group(1) if m.group(1) in by else "refactor"
        text = m.group(3).strip()
        text = text[0].upper() + text[1:]
        by[kind].append("- **%s:** %s" % (kind, text.rstrip(".") + "."))
    out = []
    for k, title in GROUPS:
        if by[k]:
            out += ["### " + title] + by[k] + [""]
    return "\n".join(out).strip()


def changelog_section(tag):
    """(summary, body) of a version's CHANGELOG.md section."""
    text = open(CHANGELOG, encoding="utf-8").read()
    m = re.search(r"^## %s\b[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(tag), text, re.M | re.S)
    if not m:
        sys.exit("CHANGELOG.md has no section for %s" % tag)
    body = m.group(1).strip()
    first, _, rest = body.partition("\n\n")
    return (first.strip(), rest.strip()) if not first.startswith("#") else ("", body)


def cmd_notes(a):
    if a.nightly:
        tag = last_tag()
        notes = draft_notes(tag) or "No user-facing changes."
        print("The newest changes on main, built as they land: new fixes sooner, less tested than a release. "
              "Settings → Update channel → Nightly follows it.\n\nSince %s:\n\n%s" % (tag or "the start", notes))
    else:
        summary, body = changelog_section(a.tag)
        print((summary + "\n\n" if summary else "") + body + "\n\n" + INSTALL)


def cmd_title(a):
    summary, _ = changelog_section(a.tag)
    print(a.tag + (": " + summary.rstrip(".") if summary else ""))


def cmd_changelog(a):
    print(draft_notes(last_tag()) or "(no user-facing changes since %s)" % last_tag())


# ---------------------------------------------------------------- checks

def require_clean_main():
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("not on main")
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("uncommitted changes: commit or stash them first")
    if git("config", "core.hooksPath") != "tools/hooks":
        sys.exit("hooks not enabled: run tools/release.py setup")
    git("fetch", "-q", "--force", "origin", "main", "--tags")       # the nightly tag moves on GitHub
    if git("rev-list", "--count", "HEAD..origin/main") != "0":
        sys.exit("origin/main has commits this clone doesn't: pull first")


def cmd_setup(a):
    run("git", "config", "core.hooksPath", "tools/hooks")
    email = git("config", "public.email")
    pats = git("config", "--get-all", "public.deny") if email else ""
    me = git("config", "user.email")
    problems = []
    if not email:
        problems.append("git config public.email <the public commit address>")
    if not pats:
        problems.append("git config --add public.deny '<regex>'   (one per private detail)")
    if email and me != email:
        problems.append("git config user.email %s   (now %s)" % (email, me))
    if problems:
        sys.exit("hooks enabled; still to set in this clone:\n  " + "\n  ".join(problems))
    say("hooks enabled; commits as %s, %d private patterns checked" % (email, len(pats.splitlines())))


def cmd_push(a):
    require_clean_main()
    ahead = git("rev-list", "--count", "origin/main..HEAD")
    if ahead == "0":
        say("main is already on GitHub")
        return
    run("python3", "tools/check_public.py", "--range", "origin/main..HEAD", change=False)
    say("pushing %s commit(s) to GitHub main" % ahead)
    run("git", "push", "origin", "HEAD:main")


def watch(workflow):
    if DRY:
        return
    import time
    time.sleep(5)
    rid = run("gh", "run", "list", "-R", REPO, "-w", workflow, "-L", "1", "--json", "databaseId",
              "--jq", ".[0].databaseId", capture=True, change=False)
    run("gh", "run", "watch", rid, "-R", REPO, "--exit-status", change=False)


def cmd_nightly(a):
    cmd_push(a)
    say("building the nightly now")
    run("gh", "workflow", "run", "nightly.yml", "-R", REPO, "-f", "force=true")
    if a.wait:
        watch("nightly.yml")


def cmd_docs(a):
    run("gh", "workflow", "run", "docs.yml", "-R", REPO, "--ref", "main")
    say("docs site: https://fraygox.github.io/romm-o-cera/ (a minute or two)")


def screenshots():
    py = shlex.split(os.environ.get("ROMM_PY", "python3"))
    out = os.path.join(HERE, "build", "shots")
    run(*py, "tools/shots.py", out, "--styles", "xbox", "--readme")


def cmd_screenshots(a):
    screenshots()
    if git("status", "--porcelain", "screenshots"):
        run("git", "add", "screenshots")
        run("git", "commit", "-q", "-m", "docs: refresh the README screenshots")
    else:
        say("screenshots unchanged")


def bump(ver, how):
    major, minor, patch = (int(x) for x in ver.split("."))
    return {"major": "%d.0.0" % (major + 1), "minor": "%d.%d.0" % (major, minor + 1),
            "patch": "%d.%d.%d" % (major, minor, patch + 1)}[how]


def set_version(new, today):
    p = os.path.join(HERE, "rommlib.py")
    src = open(p, encoding="utf-8").read()
    src = re.sub(r'^VERSION = "[^"]+"', 'VERSION = "%s"' % new, src, count=1, flags=re.M)
    src = re.sub(r'^RELEASE_DATE = "[^"]+"', 'RELEASE_DATE = "%s"' % today, src, count=1, flags=re.M)
    open(p, "w", encoding="utf-8").write(src)
    p = os.path.join(HERE, "README.md")
    src = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(re.sub(r"\(v\d+\.\d+\.\d+\)", "(v%s)" % new, src, count=1))


def add_changelog(tag, today, summary, notes):
    head = "# Changelog\n\nEvery stable release. Nightly builds: the commits on main since the last one.\n"
    text = open(CHANGELOG, encoding="utf-8").read() if os.path.exists(CHANGELOG) else head
    if re.search(r"^## %s\b" % re.escape(tag), text, re.M):
        sys.exit("CHANGELOG.md already has %s" % tag)
    section = "## %s (%s)\n\n%s\n\n%s\n\n" % (tag, today, summary, notes.strip())
    i = text.find("\n## ")
    text = text[:i + 1] + section + text[i + 1:] if i >= 0 else text.rstrip() + "\n\n" + section
    open(CHANGELOG, "w", encoding="utf-8").write(text)


def cmd_stable(a):
    require_clean_main()
    old = version()
    new = a.version or bump(old, a.bump)
    tag = "v" + new
    if git("tag", "--list", tag):
        sys.exit("%s exists already" % tag)
    today = datetime.date.today().isoformat()
    notes = open(a.notes, encoding="utf-8").read() if a.notes else draft_notes(last_tag())
    if not notes.strip():
        sys.exit("no user-facing changes since %s: nothing to release" % last_tag())
    summary = a.summary or ""
    if not summary:
        first = next((ln for ln in notes.splitlines() if ln.startswith("- ")), "- " + tag)
        summary = re.sub(r"^- (\*\*\w+:\*\*\s*)?", "", first).split(". ")[0].rstrip(".") + "."
    say("%s -> %s: %s" % (old, new, summary))
    if DRY:
        print("\n" + notes)
        return
    set_version(new, today)
    add_changelog(tag, today, summary, notes)
    if not a.no_screenshots:
        screenshots()
    run("git", "add", "rommlib.py", "README.md", "CHANGELOG.md", "screenshots")
    run("git", "commit", "-q", "-m", "chore: %s" % tag)
    run("git", "tag", tag)
    run("python3", "tools/check_public.py", "--range", "origin/main..HEAD", change=False)
    run("git", "push", "origin", "HEAD:main", tag)
    say("pushed %s: release.yml tests, builds and publishes it; docs.yml updates the site" % tag)
    if a.wait:
        watch("release.yml")


def main():
    global DRY
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "push", "docs", "screenshots", "changelog"):
        sub.add_parser(name)
    p = sub.add_parser("nightly")
    p.add_argument("--wait", action="store_true")
    p = sub.add_parser("stable")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    g.add_argument("--version")
    p.add_argument("--notes")
    p.add_argument("--summary")
    p.add_argument("--no-screenshots", action="store_true")
    p.add_argument("--wait", action="store_true")
    p = sub.add_parser("notes")
    p.add_argument("tag", nargs="?")
    p.add_argument("--nightly", action="store_true")
    p = sub.add_parser("title")
    p.add_argument("tag")
    a = ap.parse_args()
    DRY = a.dry_run
    if a.cmd == "stable" and a.version and not re.match(r"^\d+\.\d+\.\d+$", a.version):
        sys.exit("--version wants X.Y.Z")
    {"setup": cmd_setup, "push": cmd_push, "nightly": cmd_nightly, "stable": cmd_stable, "docs": cmd_docs,
     "screenshots": cmd_screenshots, "changelog": cmd_changelog, "notes": cmd_notes,
     "title": cmd_title}[a.cmd](a)


if __name__ == "__main__":
    main()
