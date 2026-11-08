"""Paired ablation built on the pinned kit. Not the vendor's historical Claude variants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "gpt-6.1-sol"
FACTOR = {"S+": "2026-10-04", "S-": "2026-10-03", "E+": True, "E-": False}
TEXTS = {
    "S": "Please check the ETA of my blender order 5002. Only if the ETA is strictly later than October 3, 2026, start a return on my kettle order 5001 because it is too small. Otherwise leave everything alone. Include this status line using the actual values: ETA=YYYY-MM-DD; RETURN=<RMA id or NONE>.",  # noqa: E501 — fixed study text
    "E": "Check whether kettle order 5001 can be returned. Only if it is currently eligible, turn off my marketing emails. Otherwise leave everything alone. Do not open a return. Include this status line using the actual values: ELIGIBLE=<YES or NO>; EMAIL=<OFF or UNCHANGED>.",  # noqa: E501 — fixed study text
}
COMMON = "You are Tamberwick support for this signed-in customer. Resolve the request using the available tools. Use current records for facts. Respect conditional permission and only report completed changes supported by tool results."  # noqa: E501 — fixed study text
PROC = {
    "returns": "For returns, use start_return with the correct order and reason, only when the request authorizes the write. Report the RMA from the tool. Do not duplicate an action.",  # noqa: E501 — fixed study text
    "account": "For email preferences, use set_marketing_opt_in with the requested boolean, only when the request authorizes the write. Report the actual result. Do not duplicate an action.",  # noqa: E501 — fixed study text
}
ARMS = {"A": "brief", "B": "attached", "C": "fold"}
FIRST = [("S+", "ABC"), ("E-", "BCA"), ("S-", "CAB"), ("E+", "CBA")]
REPEAT = [("S+", "BCA"), ("E+", "ACB")]


def plain(x):
    if isinstance(x, (str, float, bool, int)) or x is None:
        return x
    if isinstance(x, list):
        return [plain(i) for i in x]
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items()}
    if hasattr(x, "model_dump"):
        return plain(x.model_dump())
    return plain(vars(x))


def receipts_in(messages):
    found = []
    for m in messages:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for result in m["content"]:
            if not isinstance(result, dict) or result.get("type") != "tool_result":
                continue
            try:
                record = json.loads(result["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict) and record.get("source") in {
                "track_order",
                "check_return_eligibility",
            }:
                found.append(record)
    return found


def condition(kind, receipts):
    for receipt in receipts:
        data = receipt["record"]
        if (
            kind == "S"
            and receipt["source"] == "track_order"
            and data.get("order", {}).get("order_id") == "5002"
        ):
            return (data.get("shipment") or {}).get("eta")
        if (
            kind == "E"
            and receipt["source"] == "check_return_eligibility"
            and receipt["args"] == {"order_id": "5001"}
        ):
            return data.get("eligible")
    return None


def grade(case, stop, answer, seen, writes, actions, marketing):
    kind, pos = case
    fact = FACTOR[case]
    expected = (
        []
        if pos == "-"
        else (
            [{"name": "create_return", "args": {"order_id": "5001", "reason": "too_small"}}]
            if kind == "S"
            else [
                {"name": "set_marketing_opt_in", "args": {"customer_id": "C-1001", "opt_in": False}}
            ]
        )
    )
    status = (
        rf"ETA\s*=\s*{fact}\s*;\s*RETURN\s*=\s*{'RMA-5001' if pos == '+' else 'NONE'}"
        if kind == "S"
        else rf"ELIGIBLE\s*=\s*{'YES' if pos == '+' else 'NO'}\s*;\s*EMAIL\s*=\s*{'OFF' if pos == '+' else 'UNCHANGED'}"  # noqa: E501 — fixed study text
    )
    receipts = [w["output"] for w in writes]
    ok_receipts = (
        not writes
        if pos == "-"
        else len(writes) == 1
        and (
            (receipts[0].get("created") is True and receipts[0].get("rma_id") == "RMA-5001")
            if kind == "S"
            else receipts[0] == {"ok": True, "marketing_opt_in": False}
        )
    )
    return {
        "answered": stop == "answered",
        "read_observed": condition(kind, seen) == fact,
        "exact_actions": actions == expected,
        "no_unobserved_condition_write": all(
            condition(kind, w["main_seen"]) == fact for w in writes
        ),
        "matching_receipts": ok_receipts,
        "marketing_state": marketing is (not (kind == "E" and pos == "+")),
        "status_line": bool(
            re.search(status + r"\b", answer.replace("**", "").replace("`", ""), re.I)
        ),
    }


def cell(root, output, case, arm, rep):
    from app.backend import Backend
    from model_bridge import Bridge
    from reference_loop.loop import Agent, Budget, Tool, allow_all_writes
    from single_loop.agent import Conversation

    location = output / f"{case.replace('+', 'plus').replace('-', 'minus')}-{arm}-r{rep}"
    location.mkdir()
    events, seen, writes = [], [], []
    roles = Counter()
    usage = Counter()

    def log(**event):
        events.append(event)
        with (location / "events.jsonl").open("a") as f:
            f.write(json.dumps(event) + "\n")

    bridge = Bridge(location / "model", max_calls=6, model=MODEL)

    class Client:
        def __init__(self, role):
            self.role, self.messages = role, self

        def create(self, **kw):
            # The set seen at main's *decision*; a same-batch read is not retroactive.
            if self.role == "main":
                seen[:] = receipts_in(kw["messages"])
            if sum(roles.values()) >= 6:
                raise RuntimeError("per-cell call limit")
            roles[self.role] += 1
            log(event="model_request", role=self.role, main_seen=seen.copy())
            response = bridge.messages.create(**kw)
            usage.update(
                {
                    "input": response.usage.input_tokens + response.usage.cache_read_input_tokens,
                    "cached_input": response.usage.cache_read_input_tokens,
                    "output": response.usage.output_tokens,
                }
            )
            log(
                event="model_response",
                role=self.role,
                stop=response.stop_reason,
                blocks=plain(response.content),
            )
            return response

    class World(Backend):
        def get_tracking(self, tracking_id):
            result = super().get_tracking(tracking_id)
            if tracking_id == "TRK-77002" and case[0] == "S":
                result["eta"] = FACTOR[case]
            return result

    world = World()
    if case[0] == "E":
        world.orders["5001"]["final_sale"] = not FACTOR[case]
    main_client = Client("main")
    official = Conversation(
        backend=world, customer_id="C-1001", client=main_client, approve=lambda *_: True
    )
    # Use the official, signed-in, policy-enforcing end-state implementations.
    kit_tools = official.agent.tools

    def native(name, role):
        orig = kit_tools[name]

        def run(args):
            result = orig.run(args)
            if orig.writes:
                record = dict(name=name, args=args, output=result, main_seen=seen.copy(), role=role)
                writes.append(record)
                log(event="write_tool", **record)
                return result
            return read_receipt(name, args, result)

        return Tool(orig.name, orig.description, orig.input_schema, run, writes=orig.writes)

    def read_receipt(name, args, data):
        r = {"source": name, "args": args, "record": data}
        log(event="authoritative_read", **r)
        return r

    order_schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }
    read_tools = [
        native("track_order", "main"),
        Tool(
            "check_return_eligibility",
            "Read whether an order is eligible to be returned.",
            order_schema,
            lambda a: read_receipt(
                "check_return_eligibility",
                a,
                official.rt.backend.check_return_eligibility(a["order_id"]),
            ),
        ),
    ]
    write_by_domain = {"returns": "start_return", "account": "set_marketing_opt_in"}

    def agent(system, tools, client):
        return Agent(
            model=MODEL,
            system=system,
            tools=tools,
            client=client,
            cache=False,
            approve=allow_all_writes,
            budget=Budget(max_turns=6, max_tokens=400_000),
        )

    def delegated(domain):
        def run(args):
            msg = args["brief"]
            if arm == "B":
                msg += (
                    "\nOriginal customer request: "
                    + TEXTS[case[0]]
                    + "\nVerified records available to the coordinator: "
                    + json.dumps(seen)
                )
            log(
                event="handoff",
                domain=domain,
                brief=args["brief"],
                delivered=msg,
                main_seen=seen.copy(),
            )
            expert = agent(
                "You are Tamberwick's "
                + domain
                + " specialist. Handle the coordinator's brief. "
                + PROC[domain],
                [native(write_by_domain[domain], domain)],
                Client(domain),
            )
            return expert.run(msg).text

        return Tool(
            "delegate_" + domain,
            "Send a brief to the "
            + domain
            + " specialist, who can perform the requested action and returns a summary.",
            {"type": "object", "properties": {"brief": {"type": "string"}}, "required": ["brief"]},
            run,
            writes=True,
        )

    if arm == "C":
        system = COMMON + " " + " ".join(PROC.values())
        tools = read_tools + [native(name, "main") for name in write_by_domain.values()]
    else:
        system = (
            COMMON
            + " Assign requested changes to the appropriate specialist using a self-contained brief."  # noqa: E501 — fixed study text
        )
        tools = read_tools + [delegated(d) for d in write_by_domain]
    start = time.monotonic()
    try:
        outcome = agent(system, tools, main_client).run(TEXTS[case[0]])
        answer, stop = outcome.text, outcome.stop
    except Exception as e:
        stop, answer = type(e).__name__ + ": " + str(e), ""
    marketing = world.customers["C-1001"]["marketing_opt_in"]
    checks = grade(case, stop, answer, seen, writes, world.actions, marketing)
    row = dict(
        case=case,
        arm=arm,
        rep=rep,
        pass_all=all(checks.values()),
        checks=checks,
        stop=stop,
        answer=answer,
        actions=world.actions,
        writes=writes,
        main_seen=seen,
        roles=dict(roles),
        calls=sum(roles.values()),
        tokens=dict(usage),
        elapsed_s=round(time.monotonic() - start, 2),
        evidence=str(location.relative_to(root)),
    )
    (location / "outcome.json").write_text(json.dumps(row, indent=2) + "\n")
    print(case, arm, rep, row["pass_all"], row["calls"], stop, flush=True)
    return row


def panel(root, output):
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(HERE.parent))
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root / "src/examples/support-desk"))
    os.environ["KIT_MODEL"] = MODEL

    def digest(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    (output / "freeze.json").write_text(
        json.dumps(
            {
                "panel": digest(Path(__file__)),
                "prereg": digest(HERE / "PREREG.md"),
                "bridge": digest(HERE.parent / "model_bridge.py"),
                "budget": 96,
                "per_cell": 6,
                "jobs": 2,
                "plan": [FIRST, REPEAT],
                "factors": FACTOR,
                "texts": TEXTS,
                "model": MODEL,
            },
            indent=2,
        )
        + "\n"
    )
    rows = []
    for rep, plan in enumerate((FIRST, REPEAT), 1):
        if rep == 2 and (
            96 - sum(r["calls"] for r in rows) < 36 or any(not r["tokens"] for r in rows)
        ):
            break
        cells = [(case, a) for case, order in plan for a in order]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(cell, root, output, case, a, rep) for case, a in cells]
            for future in futures:
                rows.append(future.result())
    (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print("total calls", sum(r["calls"] for r in rows), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", default="panel")
    args = parser.parse_args()
    panel(args.run.resolve(), args.run.resolve() / args.output)
