"""Run the same upstream t11 conversation through both unmodified native app entrypoints."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

HERE=Path(__file__).resolve().parent
MODEL="gpt-6.1-sol"

def run(root,panel,out):
    sys.path[:0]=[str(HERE.parent),str(root/"src"),str(root/"src/examples/support-desk")]
    from model_bridge import Bridge
    from evals.run_eval import run_case
    os.environ["KIT_MODEL"]=MODEL
    rows=json.loads((panel/"results.json").read_text())
    prior=len(list(panel.glob("*/model/*/receipt.json")))
    if prior!=sum(r["calls"] for r in rows):
        raise ValueError("panel budget receipts disagree; stop")
    out.mkdir(exist_ok=False)
    (out/"freeze.json").write_text(json.dumps({"prior_calls":prior,"budget":100,"per_native_variant":14,"task":"t11",
        "prereg_sha256": hashlib.sha256((HERE/"NATIVE-LIVE-PLAN.md").read_bytes()).hexdigest(),
        "harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model":MODEL,"utc":datetime.now(timezone.utc).isoformat()},indent=2)+"\n")
    if prior+28>100:
        (out/"skipped.txt").write_text("Fewer than 28 requests available. Both official native cells skipped.\n")
        return
    tasks=(root/"src/examples/support-desk/evals/golden.jsonl").read_text().splitlines()
    task=next(t for t in map(json.loads,tasks) if t["id"]=="t11")
    def one(name):
        loc=out/name
        loc.mkdir()
        bridge=Bridge(loc/"model",max_calls=14,model=MODEL)
        factory=importlib.import_module(name).new_conversation
        try:
            row,trace=run_case(task,lambda backend,meter,**kw:factory(backend=backend,meter=meter,client=bridge,**kw))
        except Exception as e:
            row,trace={"status":"error","error":repr(e),"model_calls":bridge.calls},[]
        (loc/"outcome.json").write_text(json.dumps(row,indent=2)+"\n")
        (loc/"trace.json").write_text(json.dumps(trace,indent=2)+"\n")
        return {"app":name,"calls":bridge.calls,"status":row["status"],"grade":row.get("grade")}
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed=list(pool.map(one,["app","single_loop"]))
    print(json.dumps(completed),flush=True)
    (out/"summary.json").write_text(json.dumps(completed,indent=2)+"\n")

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("run",type=Path);p.add_argument("--panel",default="panel");p.add_argument("--out",default="native-live")
    a=p.parse_args();root=a.run.resolve();run(root,root/a.panel,root/a.out)
