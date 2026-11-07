"""Combine completed confirmation batches without changing grades or dropping failures."""

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifests, rows, hashes = [], [], {}
    for batch in args.batches:
        validity = batch / "VALIDITY.json"
        if validity.exists() and not json.loads(validity.read_text()).get(
            "eligible_for_architecture_comparison", True
        ):
            raise ValueError(f"Known apparatus defect; retain raw data separately: {batch}")
        manifest = json.loads((batch / "manifest.json").read_text())
        data = (batch / "results.jsonl").read_bytes()
        current = [json.loads(line) for line in data.decode().splitlines()]
        if (
            not manifest.get("source_unchanged")
            or manifest.get("not_run")
            or manifest.get("budget_interrupted")
            or manifest.get("usage_unknown")
            or len(current) != manifest["planned_trials"]
        ):
            raise ValueError(f"Incomplete or invalid comparison batch: {batch}")
        if manifests:
            for key in ("model", "effort", "fingerprint", "packages", "protocol", "split", "jobs"):
                if manifest[key] != manifests[0][key]:
                    raise ValueError(f"Batch configurations differ: {key}")
        manifests.append(manifest)
        hashes[str(batch)] = hashlib.sha256(data).hexdigest()
        rows.extend({**r, "batch": str(batch)} for r in current)
    groups = defaultdict(list)
    for row in rows:
        groups[row["variant"]].append(row)
    summary = {}
    for name, items in groups.items():
        summary[name] = {
            "tasks": len({r["task_id"] for r in items}),
            "trials": len(items),
            "passed": sum(r["grade"]["passed"] for r in items),
            "errors": sum(bool(r["error"]) for r in items),
            "mean_latency_s": statistics.mean(r["latency_s"] for r in items),
            "median_latency_s": statistics.median(r["latency_s"] for r in items),
            "mean_calls": statistics.mean(r["model_calls"] for r in items),
            "mean_tokens": statistics.mean(r["input_tokens"] + r["output_tokens"] for r in items),
            "skill_loaded_trials": sum(bool(r["skills_loaded"]) for r in items),
            "failed": [
                {"task": r["task_id"], "batch": r["batch"], "reasons": r["grade"]["failures"]}
                for r in items
                if not r["grade"]["passed"]
            ],
        }
    result = {
        "model": manifests[0]["model"],
        "effort": manifests[0]["effort"],
        "input_sha256": hashes,
        "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "total_calls": sum(m["actual_calls"] for m in manifests),
        "total_tokens": sum(m["actual_tokens"] for m in manifests),
        "variants": summary,
        "note": "Task/trial counts are explicit; only identical frozen protocols are combined. "
        "Different repetition counts are not interchangeable evidence strength.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
