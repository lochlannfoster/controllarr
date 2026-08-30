#!/usr/bin/env python3
"""Fail (exit 1) when a Markdown file links to a relative path that does not exist — or, from a file git
carries, to one it does not.

Used by check-file.sh after every edit of a .md file, so a renamed doc or script cannot leave a dangling
link behind. Anchors (#...), URLs with a scheme and mailto: are ignored.

The second check is the one a working tree hides: a published doc linking to a file that exists here and
nowhere else reads fine locally and is broken for everyone who clones. It applies only when the linking file
is itself tracked — an untracked note (CLAUDE.md, .claude/*.md) may link to whatever it likes — and is
skipped entirely without git or outside a checkout.
"""
import os
import re
import subprocess
import sys

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def _git(root, *args):
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=10)
    except Exception:
        return None


def _root(path):
    """The checkout `path` sits in, or None (no git, or not a checkout: the tracking check is skipped)."""
    r = _git(os.path.dirname(os.path.abspath(path)) or ".", "rev-parse", "--show-toplevel")
    return r.stdout.strip() if r and r.returncode == 0 and r.stdout.strip() else None


def _tracked(root, abspath):
    r = _git(root, "ls-files", "--error-unmatch", "--", abspath)
    return bool(r and r.returncode == 0)


def main(path: str) -> int:
    text = open(path, encoding="utf-8").read()
    # ignore fenced code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    base = os.path.dirname(os.path.abspath(path))
    root = _root(path)
    # only a file the repository carries has to link to files the repository carries
    check_tracked = bool(root) and _tracked(root, os.path.abspath(path))
    broken, untracked = [], []
    for m in LINK.finditer(text):
        target = m.group(1)
        if target.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        rel = target.split("#", 1)[0]
        if not rel:
            continue
        full = os.path.normpath(os.path.join(base, rel))
        if not os.path.exists(full):
            broken.append(target)
        elif check_tracked and not os.path.isdir(full) and not _tracked(root, full):
            untracked.append(target)
    if broken:
        print(f"{path}: {len(broken)} broken relative link(s):")
        for b in broken:
            print(f"  {b}")
    if untracked:
        print(f"{path}: {len(untracked)} link(s) to a file git does not carry (broken for anyone who clones):")
        for u in untracked:
            print(f"  {u}")
    return 1 if (broken or untracked) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
