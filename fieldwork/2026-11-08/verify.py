"""Reconcile model-call counts against original CLI events, project outcomes and launch files."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
FREEZE = "3e77b654bb5030591c8e4f791240792e43be393d"


def read(path):
    return json.loads(path.read_text())


def verify(raw):
    kit = read(HERE / "kit/summary.json")
    commerce = read(HERE / "commerce/results.json")
    aidless = read(HERE / "aidless/results.json")
    sql = read(HERE / "langchain-reference/results.json")
    haty_plan = read(raw / "haty/panel-1/plan.json")
    haty = [read(p) for p in (raw / "haty/panel-1").glob("r*/result.json")]
    assert sorted((h["rep"], h["task"], h["arch"]) for h in haty) == sorted(
        tuple(x) for x in haty_plan["schedule"]
    )
    expected = {
        "kit": kit["new_live"]["all_calls_including_native_t11"],
        "haty": sum(len(h["calls"]) for h in haty),
        "commerce": commerce["transport"]["completed"],
        "aidless": sum(v["calls"] for v in aidless["live_arms"].values()),
        "langchain-reference": sum(v["model_calls"] for v in sql["cells"]),
        "bridge-probe": 1,
    }
    cli, ids = {}, set()
    for group, n in expected.items():
        totals = dict(calls=0, input_tokens=0, cached_input_tokens=0, output_tokens=0)
        # Do not scan virtual environments, fixtures or copied upstream runs.
        patterns = {
            "kit": ["panel/*/model/*/receipt.json", "native-live/*/model/*/receipt.json"],
            "haty": ["panel-1/r*/model/*/receipt.json"],
            "commerce": ["live-1/bridge/*/receipt.json"],
            "aidless": ["live-v1/model/*/receipt.json"],
            "langchain-reference": ["live-v1/model/*/receipt.json"],
            "bridge-probe": ["**/receipt.json"],
        }
        files = sorted(p for glob in patterns[group] for p in (raw / group).glob(glob))
        assert len(files) == n, (group, len(files), n)
        for p in files:
            r = read(p)
            assert r["actual_model"] == "gpt-6.1-sol" and r["effort"] == "low"
            assert r["status"] == "ok" and r["id"] not in ids
            ids.add(r["id"])
            events = [
                json.loads(s)
                for s in (p.parent / "stdout.jsonl").read_text().splitlines()
                if s.startswith("{")
            ]
            completed = [e for e in events if e["type"] == "turn.completed"]
            assert len(completed) == 1 and completed[0]["usage"] == r["usage"]
            assert not any(e["type"] in {"error", "turn.failed"} for e in events)
            totals["calls"] += 1
            for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
                totals[key] += r["usage"][key]
        cli[group] = totals
    bridge = kit["identity"]["bridge_at_freeze_sha256"]
    assert bridge == aidless["bridge_sha256"] == haty_plan["bridge_sha256"]
    sha = {
        "model_bridge.py": bridge,
        "aidless/live.py": aidless["frozen_live_sha256"],
        "kit/panel.py": kit["identity"]["panel_sha256"],
        "kit/native_live.py": read(raw / "kit/native-live/freeze.json")["harness_sha256"],
        "haty/panel.py": haty_plan["panel_sha256"],
        "langchain-reference/probe.py": sql["script_at_launch"],
    }
    for name, wanted in sha.items():
        blob = subprocess.check_output(
            ["git", "show", f"{FREEZE}:fieldwork/2026-11-08/{name}"], cwd=LAB
        )
        assert hashlib.sha256(blob).hexdigest() == wanted, name
    return {
        "model": "gpt-6.1-sol",
        "effort": "low",
        "launch_git_commit": FREEZE,
        "recorded_launch_sha256_verified": sha,
        "request_counts": cli,
        "four_project_model_calls": sum(
            expected[g] for g in ("kit", "haty", "commerce", "aidless")
        ),
        "supplement_sql_model_calls": expected["langchain-reference"],
        "separate_transport_probe": expected["bridge-probe"],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw", type=Path)
    print(json.dumps(verify(ap.parse_args().raw), indent=2))
