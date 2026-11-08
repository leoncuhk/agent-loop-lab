"""Rerun an explicitly ordered panel against the archived upstream bench. See README.md."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
MODEL = "gpt-6.1-sol"
PIN = "d8a1d8183c72c50ec1a8bb289c308ed8fd1d54d5"
FIRST = [("T03","A"), ("T04","A1"), ("T06","B"), ("T03","A1"), ("T04","B"), ("T06","A"), ("T03","B"), ("T04","A"), ("T06","A1")]
SCHEDULE = [(r, t, a) for r, jobs in [(1,FIRST),(2,reversed(FIRST))] for t, a in jobs]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    import anthropic
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("src", type=Path, help="archived bench/subagent-fold directory")
    parser.add_argument("out", type=Path, help="new, previously nonexistent private run directory")
    args = parser.parse_args()
    src, out = args.src.resolve(), args.out.resolve()
    for f in ["bench.py", "run.py", "tasks.json", "data.json"]:
        if not (src/f).is_file():
            parser.error(f"missing source {src/f}")
    sys.path.insert(0, str(HERE.parent))
    from model_bridge import Bridge
    sys.path.insert(0, str(src))
    os.chdir(src)
    # bench constructs two SDK clients while importing. No API clients, keys, or prices
    # used on the bridge path. Both A/A1/B call only the replaced create_with_retry.
    with patch.object(anthropic, "Anthropic", return_value=None):
        import bench
        import run
    out.mkdir(parents=True, exist_ok=False)
    cap = 130
    sem = threading.BoundedSemaphore(2)
    lock = threading.Lock()
    launched = 0
    errors = []
    files = [*src.glob("*.py"), *src.glob("*.json"), *src.glob("skills/*.md")]
    plan = {"source_pin":PIN, "source_sha256":{str(p.relative_to(src)):sha(p) for p in sorted(files)},
            "prereg_sha256":sha(HERE/"prereg.md"), "panel_sha256":sha(Path(__file__).resolve()),
            "bridge_sha256":sha(HERE.parent/"model_bridge.py"), "max_model_calls":cap, "in_flight_max":2,
            "per_call_timeout_seconds":240, "actual_model":MODEL, "source_default_model":bench.AGENT_MODEL,
            "anthropic_sdk_version":anthropic.__version__, "schedule":SCHEDULE}
    write(out/"plan.json", plan)
    tasks = {t["id"]:t for t in run.TASKS}
    summary = []
    for rep, task, arch in SCHEDULE:
        if launched >= cap or errors:
            break
        key = f"r{rep}-{task}-{arch}"
        folder = out/key
        folder.mkdir()
        bridge = Bridge(folder/"model", max_calls=cap-launched, model=MODEL)
        started = time.monotonic()
        index = launched
        def create(**kw):
            nonlocal launched
            with sem:
                with lock:
                    if launched >= cap or errors:
                        raise RuntimeError("stopped: request cap or earlier infrastructure failure")
                    launched += 1
                try:
                    return bridge.messages.create(**kw)
                except Exception as e:
                    with lock:
                        errors.append({"cell":key,"exception":repr(e)})
                    raise
        bench.create_with_retry = create
        # Source run_loop is untouched, including system messages and batch execution.
        # Source trace's model arg is a bound default value. Correct the *label* only.
        try:
            res = bench.run_single(tasks[task]["user"]) if arch=="B" else bench.run_multi(tasks[task]["user"], variant=arch)
            for call in res["calls"]:
                call["source_model_arg"] = call["model"]
                call["model"] = MODEL
            rec = {"cell":key, "rep":rep, "task":task, "arch":arch, "model":MODEL, **res,
                   "source_det":run.det_checks(tasks[task],res), "bridge_errors":list(errors)}
        except Exception as e:
            rec = {"cell":key, "rep":rep,"task":task,"arch":arch,"model":MODEL,"error":repr(e), "bridge_errors":list(errors)}
            if not errors:
                errors.append({"cell":key,"exception":repr(e)})
        rec.update(attempted_model_requests=launched-index, wall_sec=round(time.monotonic()-started,3))
        write(folder/"result.json", rec)
        small = {k:rec.get(k) for k in ("cell","attempted_model_requests","source_det","error","bridge_errors")}
        summary.append(small)
        write(out/"progress.json", {"attempted":launched,"results":summary,"aborted_errors":errors})
        print(key, "calls", launched-index, "total",launched,"error",errors, flush=True)
    pending = SCHEDULE[len(summary):]
    write(out/"end.json", {"attempted_requests":launched, "cells_started":len(summary), "pending":pending, "errors":errors})
    return int(bool(pending or errors))

if __name__ == "__main__":
    raise SystemExit(main())
