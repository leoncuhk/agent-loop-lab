"""Read-only skill catalog. Loading is observable and cannot select arbitrary paths."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / "skills"


def catalog() -> dict[str, dict]:
    result = {}
    for path in sorted(ROOT.glob("*/SKILL.md")):
        _, header, body = path.read_text().split("---", 2)
        meta = yaml.safe_load(header)
        result[meta["name"]] = {"description": meta["description"], "body": body.strip()}
    return result
