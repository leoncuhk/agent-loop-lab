"""Run deterministic counterexamples *against* the pinned upstream code. No network/model."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("checkout", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    root = args.checkout.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(root))
    from agent_loop import Agent, Budget, Tool, ModelResponse, _Block
    from adapters import OpenAIModelClient
    from evalkit import compare, chain_coverage

    data = {}
    schema = {"type": "object", "properties": {}}

    def call(name, id="cmd"):
        return _Block("tool_use", name=name, id=id, input={})

    def answer(text):
        return _Block("text", text=text)

    class Script:
        def __init__(self, *steps):
            self.steps = iter(steps)

        def create(self, **kwargs):
            step = next(self.steps)
            return ModelResponse([step], "tool_use" if step.type == "tool_use" else "end_turn",
                                 {"input_tokens": 5, "output_tokens": 1})

    writes = []
    tool = Tool("save", "write synthetic row", schema, lambda _: writes.append("row"), writes=True)
    refused_init = False
    try:
        Agent(system="test", tools=[tool], client=Script())
    except ValueError:
        refused_init = True
    assert refused_init
    refused = Agent(system="test", tools=[tool], client=Script(call("save"), answer("saved")),
                    approve=lambda *_: False).run("test")
    assert not writes and refused.text == "saved"
    data["denial"] = {"missing_gate_refused_at_init": refused_init, "actual_rows": len(writes),
                      "unchecked_model_can_claim_success": True, "stop": refused.stop}
    reject_final = Agent(system="test", tools=[tool],
                         client=Script(call("save"), answer("bad"), answer("bad again")),
                         approve=lambda *_: True, verify=lambda *_: "reject").run("test")
    assert reject_final.text == "" and reject_final.stop == "verify_failed" and writes == ["row"]
    data["verifier_after_approved_write"] = {"stop": reject_final.stop,
                                             "text_withheld": True, "actual_rows": len(writes)}

    # Use the original OpenAI translation and create() method. Only the wire endpoint is a spy.
    capture, secrets_read = [], []
    def wire(**kw):
        capture.append(kw)
        use = NS(id="d1", function=NS(name="rare_lookup", arguments="{}"))
        msg = NS(content=None, tool_calls=[use]) if len(capture) == 1 else NS(content="done", tool_calls=[])
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=5, completion_tokens=1))
    adapter = OpenAIModelClient.__new__(OpenAIModelClient)
    adapter.client = NS(chat=NS(completions=NS(create=wire)))
    adapter.model = "offline-inspection"
    agent = Agent(system="test", client=adapter, tools=[
        Tool("ordinary", "ordinary", schema, lambda _: None),
        Tool("rare_lookup", "rare entry", schema, lambda _: secrets_read.append("fixture"), deferred=True)])
    result = agent.run("test")
    exposed = [t["function"]["name"] for t in capture[0]["tools"]]
    assert "rare_lookup" in exposed and "tool_search" not in exposed
    assert secrets_read == ["fixture"] and result.stop == "answered"
    data["pinned_openai_adapter"] = {"initial_functions": exposed, "rare_runs_before_search": len(secrets_read),
                                    "probe_type": "upstream adapter, captured Chat Completions payload, no SDK network"}

    spend = {}
    for charge in (True, False):
        agent = Agent(system="test", client=Script(call("child"), answer("finished")),
                      budget=Budget(max_tokens=50), tools=[])
        def child(_, *, parent=agent, report=charge):
            if report:
                parent.charge({"input_tokens": 60, "output_tokens": 0})
            return "simulated outside cost: 60"
        agent.tools["child"] = Tool("child", "child fixture", schema, child)
        outcome = agent.run("test")
        spend[str(charge)] = {"stop": outcome.stop, "reported_own_in": outcome.input_tokens,
                              "reported_own_out": outcome.output_tokens}
    assert spend["True"]["stop"] == "token_budget" and spend["False"]["stop"] == "answered"
    data["child_usage_explicit_charge_required"] = spend

    # Keep traffic and aggregate completion unchanged; degrade a critical case, improve a neighbour.
    for variant, scores in (("before", [True,True,False,False]), ("after", [True,False,True,False])):
        p = args.output / variant
        p.mkdir()
        rows = [{"task_id": ("conditional" if i < 2 else "other"), "rep": i%2, "passed": ok,
                 "route": ["orders","shipping"], "chain": ["orders","shipping"],
                 "writes": ["extra"] if variant == "after" and i == 1 else []}
                for i, ok in enumerate(scores)]
        (p/"results.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    comp = compare.compare(args.output, "before", "after")
    assert comp["holds"] and [x["task_id"] for x in comp["flips"]] == ["other"]
    data["aggregate_false_reassurance"] = {"holds": comp["holds"], "before_rate": comp["summary_before"]["pass_rate"],
        "after_rate": comp["summary_after"]["pass_rate"], "task_flips_reported": [x["task_id"] for x in comp["flips"]],
        "critical_before": "2/2", "critical_after": "1/2", "extra_write_detected_by_independent_check": True}
    traces = args.output/"traces.jsonl"
    traces.write_text(json.dumps({"chain":["shipping","orders"]})+"\n")
    rev = chain_coverage.check(args.output,"before",traces)
    assert rev["ok"]
    traces.write_text(json.dumps({"chain":["inventory"]})+"\n")
    missing = chain_coverage.check(args.output,"before",traces)
    cli = subprocess.run([sys.executable,"-m","evalkit.chain_coverage",str(traces),str(args.output),"before"],
                         cwd=root, capture_output=True, text=True)
    assert not missing["ok"] and "FAIL" in cli.stdout and cli.returncode == 0
    data["coverage_limits"] = {"reverse_order_marked_covered": rev["ok"],
                               "absent_chain_marked_missing": not missing["ok"], "absent_chain_cli_exit": cli.returncode}
    # Current eval CLI and regression definition intentionally unchanged.
    (args.output/"checks.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
