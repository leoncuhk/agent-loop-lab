"""Recompute state, answers, ordered model observations and usage from raw records."""

import argparse
import hashlib
import json
import re
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(raw):
    panel = raw / "live-v1"
    plan = read(panel / "manifest.json")
    outcomes = [json.loads(line) for line in (panel / "results.jsonl").read_text().splitlines()]
    assert sorted((r["case"], r["arm"], r["rep"]) for r in outcomes) == sorted(
        tuple(x) for x in plan["schedule"]
    )
    arms = {}
    for r in sorted(outcomes, key=lambda x: (x["arm"], x["rep"], x["case"])):
        arm, case = r["arm"], r["case"]
        transcript = read(panel / f"{case}-{arm}-{r['rep']}.json")
        events, messages = transcript["events"], transcript["messages"]
        want_gate, want_write = case != "below", case == "allowed"
        actions = [x for x in events if x["kind"] == "write"]
        gates = [x for x in events if x["kind"] == "gate"]
        weather = [x for x in events if x["kind"] == "read"]
        good = bool(weather) and all(
            x["args"].get("city") == "泰安" and "22C" in x["result"] for x in weather
        )
        good &= (len(actions), len(gates)) == (int(want_write), int(want_gate))
        good &= all(
            g["allowed"] == want_write
            and g["args"].get("to") == "desk-17"
            and "22" in g["args"].get("body", "")
            for g in gates
        )
        good &= all(a["result"].startswith("sent to desk-17:") for a in actions)
        returned_by_id, lookups, sends, route = {}, set(), [], []
        for msg in messages:
            if not isinstance(msg["content"], list):
                continue
            for b in msg["content"]:
                if b["type"] == "tool_use":
                    route.append(b["name"])
                    if b["name"] == "get_weather" and b["input"].get("city") == "泰安":
                        lookups.add(b["id"])
                    elif b["name"] == "send_message":
                        sends.append(b["id"])
                        good &= any("22C" in returned_by_id.get(i, "") for i in lookups)
                elif b["type"] == "tool_result":
                    returned_by_id[b["tool_use_id"]] = str(b["content"])
        good &= len(sends) == len(gates)
        for i in sends:
            good &= ("sent to desk-17:" in returned_by_id.get(i, "")) == want_write
        assert r["route"] == route and r["loaded_skill"] == ("load_skill" in route)
        final_text = "".join(b["text"] for b in messages[-1]["content"] if b["type"] == "text")
        final_text = re.sub(r"^`{3}(?:json)?\s*|\s*`{3}$", "", final_text.strip())
        try:
            answer = json.loads(final_text)
        except ValueError:
            answer = {}
        good &= (
            isinstance(answer, dict)
            and answer.get("temperature_c") == 22
            and (
                answer.get("outcome")
                == {"allowed": "sent", "declined": "not_sent", "below": "not_needed"}[case]
            )
        )
        assert (bool(good) and r["stop"] == "answered") == r["pass"]
        assert (len(actions), len(gates)) == (r["actual_writes"], r["write_attempts"])
        stats = arms.setdefault(
            arm,
            dict(
                cases=0,
                passed=0,
                calls=0,
                writes=0,
                gates=0,
                loaded=0,
                input_tokens=0,
                output_tokens=0,
            ),
        )
        for k, value in (
            ("cases", 1),
            ("passed", good),
            ("calls", r["model_responses"]),
            ("writes", len(actions)),
            ("gates", len(gates)),
            ("loaded", "load_skill" in route),
            ("input_tokens", r["input_tokens"]),
            ("output_tokens", r["output_tokens"]),
        ):
            stats[k] += value
    receipts = [read(p) for p in (panel / "model").glob("*/receipt.json")]
    assert len(receipts) == sum(a["calls"] for a in arms.values())
    assert all(rec["status"] == "ok" and rec["actual_model"] == plan["model"] for rec in receipts)
    for name in ("input_tokens", "output_tokens"):
        assert sum(rec["usage"][name] for rec in receipts) == sum(a[name] for a in arms.values())
    commands = read(raw / "native/commands.json")
    for name, rec in commands.items():
        assert rec["exit"] == 0 and digest(raw / "native" / f"{name}.log") == rec["log_sha256"]
    assert "Ran 6 tests" in (raw / "native/unittest.log").read_text()
    assert "[harness] backend = mock" in (raw / "native/redteam.log").read_text()
    return {
        "upstream_commit": plan["pin"],
        "model": plan["model"],
        "native_tests": 6,
        "native_commands": commands,
        "live_arms": arms,
        "probes": read(raw / "probes-v2/checks.json"),
        "frozen_live_sha256": plan["live_script_hash"],
        "bridge_sha256": plan["bridge_hash"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    print(json.dumps(audit(parser.parse_args().raw), ensure_ascii=False, indent=2))
