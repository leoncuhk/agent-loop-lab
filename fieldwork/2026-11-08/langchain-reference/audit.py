"""Reexecute the saved SQL (no model); reconcile the manifest, tool access and CLI receipts."""

import argparse
import hashlib
import json
from pathlib import Path

from probe import SHA256, execute


def audit(root):
    def read(path):
        return json.loads(path.read_text())

    panel = root / "live-v1"
    assert hashlib.sha256((root / "upstream.md").read_bytes()).hexdigest() == SHA256
    plan, rows = read(panel / "plan.json"), read(panel / "results.json")
    assert plan["cells"] == [[r["case"], r["mode"]] for r in rows] and len(rows) == 6
    receipts = [read(p) for p in sorted((panel / "model").glob("*/receipt.json"))]
    assert len(receipts) == sum(r["model_calls"] for r in rows)
    assert all(r["status"] == "ok" and r["actual_model"] == "gpt-6.1-sol" for r in receipts)
    cells = []
    for r in rows:
        sql = (panel / f"{r['case']}-{r['mode']}.sql").read_text()
        checks = [execute(sql, r["case"], changed) for changed in [False, True]]
        assert json.loads(json.dumps(checks)) == r["checks"]
        assert all(c["passed"] for c in checks) == r["passed"]
        cells.append(
            {
                k: r[k]
                for k in (
                    "case",
                    "mode",
                    "passed",
                    "calls_made",
                    "model_calls",
                    "initial_schema_absent",
                    "retrieved_domains",
                )
            }
        )
    return {
        "source_sha256": SHA256,
        "script_at_launch": plan["script_sha256"],
        "cells": cells,
        "usage": {
            key: sum(r["usage"][key] for r in receipts)
            for key in ("input_tokens", "cached_input_tokens", "output_tokens")
        },
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("raw_dir", type=Path)
    print(json.dumps(audit(p.parse_args().raw_dir), indent=2))
