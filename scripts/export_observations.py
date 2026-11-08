"""Export only whitelisted synthetic trial identifiers, outcome flags and numeric usage."""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

BATCHES = {
    "confirmation-v1": (r"h\d{2}", {"tools", "eager", "skills", "multi"}),
    "corrected-regression-v2": (r"h\d{2}", {"tools", "eager", "skills"}),
    "hard-core-v1": (r"[PR]\d+", {"tools", "eager", "skills", "domain"}),
}
HEADER = (
    "batch",
    "task",
    "method",
    "rep",
    "outcome",
    "calls",
    "input_tokens",
    "output_tokens",
    "usage_complete",
    "skill_loads",
    "delegations",
)


def export(raw, dest):
    dest.mkdir(exist_ok=True, parents=True)
    hashes, rows = {}, []
    for batch, (pattern, methods) in BATCHES.items():
        label = f"{batch}/results.jsonl"
        data = (raw / label).read_bytes()
        hashes[label] = hashlib.sha256(data).hexdigest()
        for line in data.splitlines():
            r = json.loads(line)
            if not re.fullmatch(pattern, r["task_id"]) or r["variant"] not in methods:
                raise ValueError("Unknown task or method; review public schema before exporting")
            outcome = "pass" if r["grade"]["passed"] else "business_fail"
            if r["error"]:
                # Only a known categorical code is released; exception messages can contain paths.
                outcome = {
                    "TaskBudgetExceeded": "task_budget",
                    "SharedBudgetExceeded": "shared_stop",
                    "TimeoutExpired": "request_timeout",
                }[r["error"].split(":", 1)[0]]
            counts = [r[k] for k in ("rep", "model_calls", "input_tokens", "output_tokens")]
            if not all(type(n) is int and n >= 0 for n in counts):
                raise ValueError("Unexpected count")
            rows.append(
                (
                    batch,
                    r["task_id"],
                    r["variant"],
                    r["rep"],
                    outcome,
                    *counts[1:],
                    int(r["usage_complete"]),
                    len(r["skills_loaded"]),
                    len(r["delegations"]),
                )
            )
    with (dest / "observations.csv").open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(sorted(rows))
    (dest / "source-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_root", type=Path)
    ap.add_argument("dest", type=Path, help="new directory, never overwrite existing data")
    args = ap.parse_args()
    if args.dest.exists():
        ap.error("destination already exists")
    export(args.run_root, args.dest)
