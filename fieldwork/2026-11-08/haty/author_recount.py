"""Recount upstream CSV rows; compare with published aggregates. Never invokes an LLM."""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from statistics import mean


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def recount(src):
    root = src / "results"
    main = rows(root / "runs.csv")
    ext = rows(root / "ext_runs.csv")
    truth = json.loads((root / "summary.json").read_text())["summary"]
    truth_ext = json.loads((root / "ext_summary.json").read_text())
    ids = [(r["task"], r["arch"], r["rep"]) for r in main]
    assert len(main) == 72 and len(set(ids)) == 72 and len({r["task"] for r in main}) == 12
    metrics = {}
    for a in sorted(truth):
        rr = [r for r in main if r["arch"] == a]
        n = len(rr)
        assert n == 36
        d = {k: sum(r[k] == "True" for r in rr) for k in ("success", "det_pass", "grader_pass")}
        assert all(abs(d[k] / n - truth[a][k + "_rate"]) < 1e-8 for k in d)
        d["n"] = n
        for k, k2, tolerance in [
            ("latency_s", "latency_mean", 0.006),
            ("api_calls", "api_calls_mean", 1e-8),
            ("cost_usd", "cost_mean", 0.000006),
            ("cost_nocache_usd", "cost_nocache_mean", 0.000006),
        ]:
            v = mean(float(r[k]) for r in rr)
            assert abs(v - truth[a][k2]) <= tolerance
            d[k + "_mean_from_rounded_csv"] = v
        d["failed_cells"] = [r["task"] + "-r" + r["rep"] for r in rr if r["success"] != "True"]
        metrics[a] = d
    expected_ext = {}
    for exp in ("serial", "a1", "a2", "hard"):
        pool = [r for r in ext if r["exp"] == exp]
        sub = {}
        for pair in sorted({r["arch"] + "/" + r["task"] for r in pool}):
            g = [r for r in pool if r["arch"] + "/" + r["task"] == pair]
            sums = {
                "n": len(g),
                "success": sum(r["success"] == "True" for r in g) if exp != "hard" else None,
                "det_pass": sum(r["det_pass"] == "True" for r in g),
                "called_get_shipment": sum(":get_shipment" in r["tools_called"] for r in g),
            }
            assert all(sums[k] == truth_ext[exp][pair][k] for k in sums)
            sub[pair] = sums | {
                "calls": [int(r["api_calls"]) for r in g],
                "routes": [r["routed_or_skills"] for r in g],
            }
        expected_ext[exp] = sub
    return {
        "source": "upstream rounded CSV rows vs upstream summary JSON; no raw run logs",
        "main": metrics,
        "ext_counts": dict(Counter(r["exp"] for r in ext)),
        "ext": expected_ext,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("src", type=Path)
    a = p.parse_args()
    print(json.dumps(recount(a.src), ensure_ascii=False, indent=2))
