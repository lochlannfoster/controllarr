#!/usr/bin/env python3
"""Fail (exit 1) when a Markdown file links to a relative path that does not exist.

Used by check-file.sh after every edit of a .md file, so a renamed doc or script cannot
leave a dangling link behind. Anchors (#...), URLs with a scheme and mailto: are ignored.
"""
import os
import re
import sys

LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def main(path: str) -> int:
    text = open(path, encoding="utf-8").read()
    # ignore fenced code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    base = os.path.dirname(os.path.abspath(path))
    broken = []
    for m in LINK.finditer(text):
        target = m.group(1)
        if target.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            continue
        rel = target.split("#", 1)[0]
        if not rel:
            continue
        if not os.path.exists(os.path.normpath(os.path.join(base, rel))):
            broken.append(target)
    if broken:
        print(f"{path}: {len(broken)} broken relative link(s):")
        for b in broken:
            print(f"  {b}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
