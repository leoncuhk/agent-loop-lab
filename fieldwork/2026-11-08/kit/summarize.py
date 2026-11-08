"""Reconcile raw bridge receipts, traces, executed tools and upstream native checks."""
import argparse
import hashlib
import json
from pathlib import Path

from panel import FIRST, REPEAT, TEXTS, receipts_in, grade

HERE=Path(__file__).resolve().parent
def read(path): return json.loads(path.read_text())
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def run(root, dest):
    native=read(root/"native-receipt.json")
    assert native["archive_sha256"]==digest(root/"upstream.tar")
    assert "Ran 48 tests" in (root/"native/tests.txt").read_text()
    for s in ("router","finished"):
        assert "19 tasks ran on the offline stand-in model, 0 harness errors" in (root/f"native/smoke-{s}.txt").read_text()
    for key in ["fold","as-tool"]:
        txt=(root/f"native/coverage-{key}.txt").read_text()
        assert "No covering task passes today for: orders -> shipping -> returns" in txt
    c=(root/"native/coupling.txt").read_text(); coupling=json.loads(c[c.index("{"):])
    assert (coupling["traces"],coupling["calls"])==(200,322)
    probe=read(root/"probes/probes.json")
    panel=root/"panel"
    freeze=read(panel/"freeze.json")
    assert freeze["panel"]==digest(HERE/"panel.py") and freeze["prereg"]==digest(HERE/"PREREG.md")
    rows=read(panel/"results.json")
    assert len(rows) in (12,18)
    expected = [(1,FIRST)] + ([(2,REPEAT)] if len(rows)==18 else [])
    assert {(r["case"],r["arm"],r["rep"]) for r in rows} == {(case,arm,rep) for rep,plan in expected for case,arms in plan for arm in arms}
    state=[]
    for row in rows:
        loc=root/row["evidence"]
        assert read(loc/"outcome.json")==row
        events=[json.loads(l) for l in (loc/"events.jsonl").read_text().splitlines()]
        seen_events=[e for e in events if e["event"]=="model_request"]
        ledgers=sorted((loc/"model").glob("*/receipt.json"))
        assert len(ledgers)==row["calls"]==len(seen_events)
        requests=[read(p.parent/"request.json") for p in ledgers]
        # Verify paired main inputs have no hidden-world id / source answer.
        assert requests[0]["messages"]==[{"role":"user","content":TEXTS[row["case"][0]]}]
        assert all(read(p)["status"]=="ok" for p in ledgers)
        input_tokens=sum(read(p)["usage"]["input_tokens"] for p in ledgers)
        output_tokens=sum(read(p)["usage"]["output_tokens"] for p in ledgers)
        assert (input_tokens,output_tokens)==(row["tokens"]["input"],row["tokens"]["output"])
        hands=[e for e in events if e["event"]=="handoff"]
        if row["arm"]=="C": assert not hands
        for e in hands:
            if row["arm"]=="A": assert e["delivered"]==e["brief"]
            if row["arm"]=="B":
                assert TEXTS[row["case"][0]] in e["delivered"] and json.dumps(e["main_seen"]) in e["delivered"]
            assert any(q["messages"][0]["content"]==e["delivered"] for q in requests)
        for request, event in zip(requests,seen_events,strict=True):
            if event["role"]=="main":
                assert receipts_in(request["messages"])==event["main_seen"]
        source_writes=[{k:v for k,v in e.items() if k!="event"} for e in events if e["event"]=="write_tool"]
        assert source_writes==row["writes"]
        marketing=True
        replayed=[]
        for w in source_writes:
            if w["name"]=="start_return" and w["output"].get("created") is True:
                replayed.append({"name":"create_return","args":w["args"]})
            if w["name"]=="set_marketing_opt_in" and w["output"].get("ok") is True:
                marketing=w["output"]["marketing_opt_in"]
                replayed.append({"name":"set_marketing_opt_in","args":{"customer_id":"C-1001","opt_in":marketing}})
        assert replayed==row["actions"]
        recomputed=grade(row["case"],row["stop"],row["answer"],row["main_seen"],source_writes,replayed,marketing)
        assert recomputed==row["checks"] and all(recomputed.values())==row["pass_all"]
        last_main=None
        for e in events:
            if e["event"]=="model_request" and e["role"]=="main":last_main=e
            elif e["event"]=="write_tool":
                assert last_main is not None and last_main["main_seen"]==e["main_seen"]
        state.append({"case":row["case"],"arm":row["arm"],"rep":row["rep"],"pass":row["pass_all"],"calls":row["calls"],
                      "input":input_tokens,"output":output_tokens,"handoffs":len(hands),"writes":len(row["actions"]),
                      "failed":[k for k,v in row["checks"].items() if not v]})
    def group(members):
        return {a:{"cells":len(ss),"passes":sum(s["pass"] for s in ss),"calls":sum(s["calls"] for s in ss),
                   "input":sum(s["input"] for s in ss),"output":sum(s["output"] for s in ss),"writes":sum(s["writes"] for s in ss),
                   "handoffs":sum(s["handoffs"] for s in ss)} for a in "ABC" if (ss:=[s for s in members if s["arm"]==a])}
    live=root/"native-live"
    if (live/"summary.json").exists():
        native_runs={}
        replay=read(root/"native-replay/replay.json")
        for n in read(live/"summary.json"):
            loc=live/n["app"]; row=read(loc/"outcome.json")
            assert len(list(loc.glob("model/*/receipt.json")))==n["calls"]==row["model_calls"]
            native_runs[n["app"]]={"status":row["status"],"grade":row.get("grade"),"calls":n["calls"],
                "error":row.get("error"),"final_answer":replay[n["app"]]["customer_answer_available"],
                "input":replay[n["app"]]["input"],"output":replay[n["app"]]["output"],
                "actions_in_byte_matched_replay":replay[n["app"]]["actions"],
                "byte_matched_requests":replay[n["app"]]["byte_matched_requests"],"agents":replay[n["app"]]["roles"]}
    else:
        assert (live/"skipped.txt").exists()
        native_runs={}
    actual=sum(s["calls"] for s in state)+sum(r["calls"] for r in native_runs.values())
    assert actual<=100
    summary={"commit":native["commit"],"upstream_tar_sha256":native["archive_sha256"],"environment":{"python":native["python"],"platform":native["platform"]},
             "official_local":{"tests":48,"offline_smoke":{"app":19,"single_loop":19,"harness_errors":0,"quality_scored":False},
             "synthetic_traces":coupling["traces"],"subagent_calls":coupling["calls"],"coverage_exit":{"12_tasks":1,"17_tasks":0}},
             "published_author_rows_recounted":native["published_rows"],"counterexamples":{"native_accepts":{k:v["native_pass"] for k,v in probe["native_grader_counterexamples"].items()},
             "chain_coverage_split_rep":probe["split_rep_coverage"],"panel_accepts":{k:v["panel_pass"] for k,v in probe["panel_grader_controls"].items()}},
             "new_live":{"model":freeze["model"],"native_provider_reproduction":False,"main_panel_cells":len(rows),"calls":sum(s["calls"] for s in state),
             "input_includes_cached":True,"groups_all":group(state),"groups_first_balanced":group([s for s in state if s["rep"]==1]),
             "positive_only":group([s for s in state if s["case"][1]=="+"]),"negative_only":group([s for s in state if s["case"][1]=="-"]),
             "cells":state,"new_native_t11":native_runs,"all_calls_including_native_t11":actual},
             "identity":{"bridge_at_freeze_sha256":freeze["bridge"],"panel_sha256":freeze["panel"],"prereg_sha256":freeze["prereg"]}}
    dest.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
    print(f"Reconciled {len(rows)} ablation cells, {len(native_runs)} native cells, {actual} new model requests.")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("run",type=Path);p.add_argument("--summary",type=Path,required=True)
    a=p.parse_args();run(a.run.resolve(),a.summary.resolve())
