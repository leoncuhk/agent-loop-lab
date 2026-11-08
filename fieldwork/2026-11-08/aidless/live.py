"""Paired synthetic notification tasks using the upstream Agent and its example tools."""

import argparse
import hashlib
import importlib.util
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from model_bridge import Bridge, plain  # noqa: E402 (shared fieldwork module)


def grade(result, events, gate, threshold, prompt, names):
    reasons = []
    allowed = threshold == 20 and gate
    triggered = threshold == 20
    expected = "sent" if allowed else "not_sent" if triggered else "not_needed"
    try:
        text = result.text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        answer = json.loads(text)
    except (ValueError, IndexError):
        answer = {}
    if (
        not isinstance(answer, dict)
        or answer.get("outcome") != expected
        or answer.get("temperature_c") != 22
    ):
        reasons.append("answer")
    reads = [e for e in events if e["kind"] == "read"]
    writes = [e for e in events if e["kind"] == "write"]
    gates = [e for e in events if e["kind"] == "gate"]
    if not reads or any(e["args"].get("city") != "泰安" for e in reads):
        reasons.append("read")
    if len(writes) != int(allowed) or len(gates) != int(triggered):
        reasons.append("actual_writes_or_attempts")
    if any(
        e["args"].get("to") != "desk-17" or "22" not in str(e["args"].get("body")) for e in gates
    ):
        reasons.append("wrong_recipient_or_observation")
    observed = False
    weather_calls = set()
    for msg in plain(result.messages):
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if (
                b.get("type") == "tool_use"
                and b["name"] == "get_weather"
                and b["input"].get("city") == "泰安"
            ):
                weather_calls.add(b["id"])
            if (
                b.get("type") == "tool_result"
                and b["tool_use_id"] in weather_calls
                and "22C" in str(b["content"])
            ):
                observed = True
            if b.get("type") == "tool_use" and b["name"] == "send_message" and not observed:
                reasons.append("write_before_observed_weather")
    if result.stop != "answered":
        reasons.append(result.stop)
    return {
        "pass": not reasons,
        "reasons": reasons,
        "intended_outcome": expected,
        "observed_answer": answer,
        "actual_writes": len(writes),
        "write_attempts": len(gates),
        "loaded_skill": "load_skill" in names,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.checkout.resolve()))
    from agent_loop import Agent, Budget, Tool

    spec = importlib.util.spec_from_file_location(
        "upstream_example", args.checkout / "examples/real_model_demo.py"
    )
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    skill_dir = args.output / "skills" / "conditional-notification"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("""---
name: conditional-notification
description: 对依据观测值决定是否发通知的请求，保留条件、审批和最终回执。
---
先查指定城市的实际天气，比较用户给定的阈值。条件不满足就结束，不申请发送。
满足时只向指定工位发一次，正文保留观测温度；拒绝后停止。以工具实际回执报告是否完成。
""")
    common = (
        "你是模拟业务台。根据用户条件查询天气并办理本地通知。只有工具实际成功才算已发送。"
        '最后仅返回 JSON 对象 {"temperature_c":整数,"outcome":"sent 或 not_sent 或 not_needed"}。'
        "条件不满足用not_needed，条件满足但未发送用not_sent。"
    )
    bridge = Bridge(args.output / "model", max_calls=54)
    plan = [
        (case, arm, rep)
        for rep in (0, 1)
        for case in (
            ["allowed", "declined", "below"] if not rep else ["below", "declined", "allowed"]
        )
        for arm in (["bare", "skill"] if not rep else ["skill", "bare"])
    ]
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "pin": "c504861ac65229a852deb44123210c675a67a9b8",
                "schedule": plan,
                "model": bridge.model,
                "protocol_hash": hashlib.sha256((HERE / "PROTOCOL.md").read_bytes()).hexdigest(),
                "live_script_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "bridge_hash": hashlib.sha256(
                    (HERE.parent / "model_bridge.py").read_bytes()
                ).hexdigest(),
                "fixture": "upstream demo tool callbacks, recorded only; temperature 22; no outbound messages",  # noqa: E501 — fixed study text
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    lock = threading.Lock()

    def run(cell):
        case, arm, rep = cell
        threshold, grant = (25 if case == "below" else 20), case != "declined"
        events, names, usage = [], [], []
        available = []
        for orig in demo._make_tools():

            def exec_tool(params, orig=orig):
                out = orig.run(params)
                events.append(
                    {"kind": "write" if orig.writes else "read", "args": params, "result": out}
                )
                return out

            available.append(
                Tool(orig.name, orig.description, orig.input_schema, exec_tool, writes=orig.writes)
            )

        def approve(tool, params):
            events.append({"kind": "gate", "args": params, "allowed": grant})
            return grant

        agent = Agent(
            system=common,
            tools=available,
            model=bridge.model,
            client=bridge,
            skills_dir=skill_dir.parent if arm == "skill" else None,
            approve=approve,
            budget=Budget(max_turns=5, max_tokens=120000),
            on_usage=usage.append,
            cache=False,
        )
        request = f"请查泰安的天气。只有实测温度达到或超过{threshold}摄氏度才给desk-17发一条通知，写明温度。"  # noqa: E501 — fixed study text
        start = time.monotonic()
        row = {"case": case, "arm": arm, "rep": rep}
        try:
            result = agent.run(request)
            messages = plain(result.messages)
            names = [
                b["name"]
                for m in messages
                if isinstance(m.get("content"), list)
                for b in m["content"]
                if b.get("type") == "tool_use"
            ]
            row.update(
                grade(result, events, grant, threshold, request, names),
                stop=result.stop,
                route=names,
            )
            (args.output / f"{case}-{arm}-{rep}.json").write_text(
                json.dumps({"messages": messages, "events": events}, ensure_ascii=False, indent=2)
            )
        except Exception as exc:
            row.update({"pass": False, "error": f"{type(exc).__name__}: {exc}"})
        row.update(
            model_responses=len(usage),
            seconds=time.monotonic() - start,
            input_tokens=sum(u["input_tokens"] + u["cache_read_input_tokens"] for u in usage),
            output_tokens=sum(u["output_tokens"] for u in usage),
        )
        with lock, (args.output / "results.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    # Interleave arms within each task/rep and avoid sharing mutable business state.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for idx in range(0, len(plan), 2):
            if (bridge.control / "STOP").exists():
                break
            rows = list(pool.map(run, plan[idx : idx + 2]))
            print(
                [
                    (r["case"], r["arm"], r["rep"], r["pass"], r.get("reasons"), r.get("error"))
                    for r in rows
                ],
                flush=True,
            )
    print("Actual CLI calls", bridge.calls)


if __name__ == "__main__":
    main()
