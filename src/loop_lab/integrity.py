"""Source identity shared by batch runs and checkpoint continuation."""

import hashlib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def fingerprint() -> dict:
    files = [PROJECT / "pyproject.toml", PROJECT / "uv.lock"]
    files += sorted((PROJECT / "src").rglob("*.py"))
    files += sorted((PROJECT / "skills").rglob("*.md"))
    return {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
