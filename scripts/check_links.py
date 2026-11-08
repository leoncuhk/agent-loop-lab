"""Validate relative Markdown links against the public Git index, not ignored local files."""

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def tracked():
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return sorted(f for f in files if f)


def check_links(files):
    known = set(files)
    dirs = {str(p) for f in files for p in Path(f).parents}
    broken = []
    for f in files:
        if not f.endswith(".md"):
            continue
        for url in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", (ROOT / f).read_text()):
            if re.match("^([a-z]+:|#|//)", url):
                continue
            p = unquote(url.split("#")[0])
            target = os.path.normpath(os.path.join(os.path.dirname(f), p))
            if target not in known and target not in dirs:
                broken.append((f, url))
    if broken:
        raise ValueError(f"Links not available from a public checkout: {broken}")


if __name__ == "__main__":
    names = tracked()
    check_links(names)
    print(f"Relative Markdown paths OK ({len(names)} tracked files)")
