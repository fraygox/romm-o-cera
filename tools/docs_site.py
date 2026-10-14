#!/usr/bin/env python3
"""Assemble the documentation website's source from README.md, docs/ and
screenshots/, for mkdocs.yml (GitHub Pages builds it on every push to main).
The Markdown stays written for GitHub; this only reshapes it into pages:
README.md -> Home (intro, screenshots, what it does), Install, Credits.
    python3 tools/docs_site.py && mkdocs build --strict     (site/ is the result)
"""
import os
import re
import shutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "build", "site-src")
REPO = "https://github.com/fraygox/romm-o-cera/blob/main/"

# README sections: which page each goes to (None: not on the site; the nav lists the pages)
README_PAGES = {"What it does": "index.md", "Install": "install.md", "Credits": "credits.md",
                "License": "credits.md", "Documentation": None}


def readme_link(m):
    """A link written from README.md, for a page next to the docs pages."""
    text, target = m.group(1), m.group(2)
    if target.startswith(("http://", "https://", "#", "screenshots/")):
        return m.group(0)
    if target.startswith("docs/"):
        target = target[len("docs/"):]
        return "[%s](%s)" % (text, "index.md" if target == "README.md" else target)
    return "[%s](%s%s)" % (text, REPO, target)        # LICENSE, fonts/...: on GitHub


def docs_link(m):
    """A link written from docs/X.md."""
    text, target = m.group(1), m.group(2)
    if target.startswith("../README.md"):
        anchor = target[len("../README.md"):]
        return "[%s](%s%s)" % (text, "install.md" if anchor else "index.md", anchor)
    if target.startswith("../screenshots/"):
        return "[%s](%s)" % (text, target[3:])
    if target.startswith("../"):
        return "[%s](%s%s)" % (text, REPO, target[3:])
    return m.group(0)


LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def demote(text):
    """A README section as its own page: ## Install -> # Install, ### x -> ## x."""
    return re.sub(r"(?m)^#(#+) ", r"\1 ", text)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    head, *sections = re.split(r"(?m)^## ", readme)
    pages = {"index.md": [head.replace("# RomM-o-Cera — a RomM client for Batocera", "# RomM-o-Cera", 1)]}
    for sec in sections:
        title = sec.split("\n", 1)[0].strip()
        page = README_PAGES.get(title, "index.md")
        if page is None:
            continue
        if page == "index.md":
            body = "## " + sec
        elif page in pages:                            # a second section on the page: keep its level
            body = "## " + sec
        else:
            body = demote("## " + sec)
        pages.setdefault(page, []).append(body)
    for page, parts in pages.items():
        text = "\n".join(p.rstrip() + "\n" for p in parts)
        text = re.sub(r"All the details are on the \*\*\[documentation site\].*?\)\)\.\n", "", text, flags=re.S)
        if page == "credits.md":
            text = text.replace("# Credits", "# Credits and license", 1)
        with open(os.path.join(OUT, page), "w", encoding="utf-8") as f:
            f.write(LINK.sub(readme_link, text))
    for fn in sorted(os.listdir(os.path.join(HERE, "docs"))):
        if fn.endswith(".md") and fn != "README.md":    # the index page is the nav itself
            with open(os.path.join(HERE, "docs", fn), encoding="utf-8") as f:
                text = f.read()
            with open(os.path.join(OUT, fn), "w", encoding="utf-8") as f:
                f.write(LINK.sub(docs_link, text))
    shutil.copytree(os.path.join(HERE, "screenshots"), os.path.join(OUT, "screenshots"))
    shutil.copy(os.path.join(HERE, "icon.png"), os.path.join(OUT, "icon.png"))
    shutil.copy(os.path.join(HERE, "tools", "docs-extra.css"), os.path.join(OUT, "extra.css"))
    print("site source in", os.path.relpath(OUT, HERE))


if __name__ == "__main__":
    main()
