"""Obtain a pinned Git object for a selected external reproduction; never change a working tree."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = {
    "kit": ("anthropics/single-agent-loop-kit", "c43796287b83bec847db923f07131d1cc8de750d"),
    "haty": ("hatyibei/zenn-articles", "d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5"),
    "commerce": ("anthropics/commerce-agents", "fd4d59224ab96b43c6dc6888207c67b3bd5a24cf"),
    "aidless": ("aidless/agent-loop-safety", "c504861ac65229a852deb44123210c675a67a9b8"),
}


def reference_root():
    """Use the shared workspace checkout, or a standalone clone's sibling cache."""
    if explicit := os.getenv("LOOP_LAB_REFERENCES"):
        return Path(explicit).expanduser().resolve()
    workspace = ROOT.parent.parent
    if ROOT.parent.name == "runtime" and (workspace / "refs").is_dir():
        return (workspace / "refs/upstream").resolve()
    if ROOT.parent.name == "agent-runtime" and (workspace / "evaluation").is_dir():
        return (workspace / "evaluation/references").resolve()
    return (ROOT.parent / "references").resolve()


def git(repo, *args, **kwargs):
    return subprocess.run(["git", "-C", str(repo), *args], text=True, **kwargs)


def prepare(project, base):
    slug, rev = PINS[project]
    url = f"https://github.com/{slug}.git"
    repo = base / slug.split("/")[1]
    if repo.exists():
        # A pinned object is sufficient even for an existing fork; no writes to its checkout.
        head = git(repo, "rev-parse", "--absolute-git-dir", capture_output=True)
        if head.returncode or Path(head.stdout.strip()) not in (repo, repo / ".git"):
            raise SystemExit(f"{repo} is not an independent checkout or bare Git store")
        if git(repo, "cat-file", "-e", f"{rev}^{{commit}}", capture_output=True).returncode:
            raise SystemExit(f"Existing store lacks pin {rev}; fetch it or use a new cache")
    else:
        base.mkdir(exist_ok=True, parents=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            staging = Path(tmp) / "repo"
            subprocess.run(["git", "init", "-q", "--bare", str(staging)], check=True)
            git(staging, "fetch", "-q", "--depth=1", url, f"{rev}:refs/heads/pinned", check=True)
            git(staging, "symbolic-ref", "HEAD", "refs/heads/pinned", check=True)
            staging.rename(repo)
    actual = git(repo, "rev-parse", f"{rev}^{{commit}}", capture_output=True, check=True)
    if actual.stdout.strip() != rev:
        raise SystemExit("Git commit mismatch")
    print(f"{project}: {repo} @ {rev}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", metavar="PROJECT", help=", ".join(sorted(PINS)))
    parser.add_argument(
        "--show-cache", action="store_true", help="Print the cache path without fetching"
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=reference_root(),
    )
    args = parser.parse_args()
    unknown = set(args.projects) - PINS.keys()
    if unknown:
        parser.error("unknown project(s): " + ", ".join(sorted(unknown)))
    if args.show_cache:
        print(args.cache.expanduser().resolve())
        raise SystemExit(0)
    if not args.projects:
        parser.error("select projects or use --show-cache")
    for name in args.projects:
        prepare(name, args.cache.resolve())
