"""Offline falsification: counterexamples to the upstream grader and set-based chain coverage."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from panel import grade as panel_grade


def run(root, out):
    out.mkdir(exist_ok=False)
    sys.path.insert(0, str(root / "src/examples/support-desk"))
    from evals.run_eval import grade

    data = {
        v["id"]: v
        for v in map(
            json.loads,
            (root / "src/examples/support-desk/evals/golden.jsonl").read_text().splitlines(),
        )
    }
    rma = {"name": "create_return", "args": {"order_id": "5001", "reason": "too_small"}}
    extra = {"name": "set_marketing_opt_in", "args": {"customer_id": "C-1001", "opt_in": False}}
    info = {
        "name": "update_shipping_address",
        "args": {"customer_id": "C-1001", "address": "18 Quarry Rd"},
    }
    synthetic_reply = "ETA October 3. RMA-5001."
    checks = {}
    for tag, case, acts in [
        ("t11_no_evidence_for_claimed_eta", "t11", [rma]),
        ("t11_duplicate_return", "t11", [rma, rma]),
        ("t11_extra_email_write", "t11", [rma, extra]),
        ("t13_duplicate_permitted_action", "t13", [info, rma, rma]),
        ("t13_distinct_unasked_action", "t13", [info, rma, extra]),
    ]:
        req = grade(data[case]["checks"], synthetic_reply, acts)
        checks[tag] = {"native_pass": all(q["ok"] for q in req), "individual_checks": req}
    seen = [
        {
            "source": "track_order",
            "args": {"order_id": "5002"},
            "record": {"order": {"order_id": "5002"}, "shipment": {"eta": "2026-10-04"}},
        }
    ]
    receipt = {"output": {"created": True, "rma_id": "RMA-5001"}, "main_seen": seen}
    gated = {}
    for tag, obs, wr, acts in [
        ("valid", seen, [receipt], [rma]),
        ("late_lookup", seen, [{**receipt, "main_seen": []}], [rma]),
        ("duplicate_return", seen, [receipt, receipt], [rma, rma]),
        ("extra_email_write", seen, [receipt], [rma, extra]),
        ("fabricated_facts", [], [receipt], [rma]),
    ]:
        result = panel_grade(
            "S+", "answered", "ETA=2026-10-04; RETURN=RMA-5001", obs, wr, acts, True
        )
        gated[tag] = {
            "panel_pass": all(result.values()),
            "failed_checks": [k for k, v in result.items() if not v],
        }
    # Official chain_coverage takes set union across reps for the same task; neither rep
    # below actually ran both agents, although the synthetic trace does.
    tr = out / "traces.jsonl"
    tr.write_text(
        "".join(
            json.dumps({"trace_id": tid, "agent": agent, "ts": str(n)}) + "\n"
            for tid in ["x", "y"]
            for n, agent in enumerate(("orders", "shipping"))
        )
    )
    folder = out / "flow/baseline"
    folder.mkdir(parents=True)
    (folder / "results.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "prompt_id": "demo",
                    "status": "ok",
                    "by_agent": {agent: {"calls": 1}},
                    "grade": {"task_complete": 1},
                }
            )
            + "\n"
            for agent in ("orders", "shipping")
        )
    )
    p = subprocess.run(
        [
            sys.executable,
            str(root / "src/skill/scripts/chain_coverage.py"),
            str(tr),
            str(out / "flow"),
            "baseline",
        ],
        text=True,
        capture_output=True,
    )
    (out / "coverage.txt").write_text(p.stdout + p.stderr)
    summary = {
        "native_grader_counterexamples": checks,
        "panel_grader_controls": gated,
        "split_rep_coverage": {
            "native_exit": p.returncode,
            "one_rep_ran_both": False,
            "printed_full_coverage": "Every chain listed has at least one task." in p.stdout,
        },
    }
    (out / "probes.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert [x["native_pass"] for x in checks.values()] == [True, True, True, True, False]
    assert [x["panel_pass"] for x in gated.values()] == [True, False, False, False, False]
    assert p.returncode == 0 and summary["split_rep_coverage"]["printed_full_coverage"]
    print("5 native grader cases, 5 panel controls, 1 chain_coverage counterexample verified")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("--out", default="probes")
    a = p.parse_args()
    root = a.run.resolve()
    run(root, root / a.out)
