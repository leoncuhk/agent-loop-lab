"""Descriptive paired resource comparisons after the frozen fair holdout has finished."""

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.run / "summary.json").read_text())
    if not summary["complete_balanced"]:
        raise ValueError("Incomplete study: do not compare costs as a complete panel")
    raw = (args.run / "trials.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.splitlines()]
    for row in rows:
        row["tokens"] = row["input_tokens"] + row["output_tokens"]
    cells = {(r["task_id"], r["rep"], r["method"]): r for r in rows}
    if len(cells) != len(rows) or any(r["unknown_usage_requests"] for r in rows):
        raise ValueError("Duplicate trial or unaccounted usage")

    def matched(left, right, keep):
        pairs = [
            (r, cells[r["task_id"], r["rep"], right])
            for r in rows
            if r["method"] == left and keep(r)
        ]
        out = {
            "left": left,
            "right": right,
            "paired_trials": len(pairs),
            "distinct_tasks": len({r["task_id"] for r, _ in pairs}),
        }
        for key in ("model_calls", "tokens", "latency_s"):
            a, b = [[r[i][key] for r in pairs] for i in (0, 1)]
            out[key] = {
                "left_mean": statistics.mean(a),
                "right_mean": statistics.mean(b),
                "right_pct_vs_left": 100 * (sum(b) / sum(a) - 1),
                "right_lower_trials": sum(y < x for x, y in zip(a, b, strict=True)),
                "right_higher_trials": sum(y > x for x, y in zip(a, b, strict=True)),
            }
        return out

    result = {
        "trials_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": "Descriptive post-run secondary analysis; same case and rep in each pair.",
        "eager_vs_skills": {
            size: matched("eager", "skills", lambda r, s=size: s == "all" or r["library"] == s)
            for size in ("all", "small", "large")
        },
        "tools_vs_others": {
            m: matched("tools", m, lambda r: True) for m in ("skills", "eager", "multi")
        },
        "delegated_multi_vs_paired_tools": matched(
            "multi", "tools", lambda r: r["actual_topology"] == "delegated"
        ),
        "delegated_cases": dict(
            Counter(
                r["task_id"]
                for r in rows
                if r["method"] == "multi" and r["actual_topology"] == "delegated"
            )
        ),
        "note": "Library size differs between cases; subset differences do not isolate "
        "the causal effect of growing a library. Delegation is adaptive, so its "
        "selected subset is descriptive. Latency includes the shared queue and CLI.",
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
