"""Offline replay: verify each native request against the original and preserve truncated state."""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


class EndOfLedger(BaseException):
    pass


def run(root, out):
    sys.path[:0] = [str(HERE.parent), str(root / "src"), str(root / "src/examples/support-desk")]
    from app.backend import Backend
    from app.llm import Meter
    from evals.run_eval import grade
    from model_bridge import AttrDict, plain

    os.environ["KIT_MODEL"] = "gpt-6.1-sol"
    case = next(
        r
        for r in map(
            json.loads,
            (root / "src/examples/support-desk/evals/golden.jsonl").read_text().splitlines(),
        )
        if r["id"] == "t11"
    )
    out.mkdir(exist_ok=False)
    all_results = {}
    for app in ("app", "single_loop"):
        ledger = sorted((root / "native-live" / app / "model").glob("*/receipt.json"))
        if not ledger:
            raise ValueError("There are no native model receipts for " + app)

        class Replay:
            def __init__(self, ledger):
                self.ledger = ledger
                self.calls = 0
                self.messages = self

            def create(self, *, system, tools, messages, **ignored):
                if self.calls == len(self.ledger):
                    raise EndOfLedger()
                p = self.ledger[self.calls]
                self.calls += 1
                req = {"system": plain(system), "tools": plain(tools), "messages": plain(messages)}
                assert (
                    json.dumps(req, ensure_ascii=False) == (p.parent / "request.json").read_text()
                )
                receipt = json.loads(p.read_text())
                assert receipt["status"] == "ok"
                events = list(map(json.loads, (p.parent / "stdout.jsonl").read_text().splitlines()))
                decision = json.loads(
                    [
                        e["item"]["text"]
                        for e in events
                        if e.get("type") == "item.completed"
                        and e["item"]["type"] == "agent_message"
                    ][-1]
                )
                content = [AttrDict(type="text", text=decision["text"])] if decision["text"] else []
                for i, call in enumerate(decision["calls"]):
                    content.append(
                        AttrDict(
                            type="tool_use",
                            id=f"call-{receipt['id']}-{i}",
                            name=call["name"],
                            input=json.loads(call["arguments_json"]),
                        )
                    )
                u = receipt["usage"]
                cached = u["cached_input_tokens"]
                return AttrDict(
                    content=content,
                    stop_reason="tool_use" if decision["calls"] else "end_turn",
                    usage=AttrDict(
                        input_tokens=u["input_tokens"] - cached,
                        cache_read_input_tokens=cached,
                        output_tokens=u["output_tokens"],
                    ),
                )

        meter, world, client = Meter(), Backend(), Replay(ledger)
        conversation = importlib.import_module(app).new_conversation(
            backend=world, meter=meter, client=client, customer_id=case["customer"]
        )
        try:
            answer = conversation.send(case["turns"][0])
            stop = "answered"
        except EndOfLedger:
            answer = None
            stop = "end_of_saved_model_responses"
        assert client.calls == len(ledger)
        checked = grade(case["checks"], answer, world.actions) if answer is not None else None
        official = json.loads((root / "native-live" / app / "outcome.json").read_text())
        if official.get("status") == "ok":
            assert official["answer"] == answer and official["actions"] == world.actions
        else:
            assert (
                stop == "end_of_saved_model_responses" and "budget exhausted" in official["error"]
            )
        (out / (app + "-trace.json")).write_text(json.dumps(conversation.trace, indent=2) + "\n")
        all_results[app] = {
            "byte_matched_requests": client.calls,
            "stop": stop,
            "customer_answer_available": answer is not None,
            "actions": world.actions,
            "native_pass_if_answered": None if checked is None else all(x["ok"] for x in checked),
            "input": meter.input_tokens,
            "output": meter.output_tokens,
            "roles": {k: v["calls"] for k, v in meter.by_agent.items()},
        }
    (out / "replay.json").write_text(json.dumps(all_results, indent=2) + "\n")
    print(json.dumps(all_results))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("--out", default="native-replay")
    a = p.parse_args()
    r = a.run.resolve()
    run(r, r / a.out)
