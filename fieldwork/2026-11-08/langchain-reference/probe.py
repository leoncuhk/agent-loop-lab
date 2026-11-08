"""Run the tutorial's original Skill and middleware; check generated SQL in SQLite."""

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import sys
import typing
from pathlib import Path
from urllib.request import urlopen

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import AIMessage, SystemMessage
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from loop_lab.models import CodexBridge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_bridge import Bridge

URL = "https://docs.langchain.com/oss/python/langchain/multi-agent/skills-sql-assistant.md"
SHA256 = "6e94f5058953b3c24be21b987bf85ff3af484871872f97b73f6d10c4eb14acb3"


def original_parts(path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(urlopen(URL, timeout=25).read())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SHA256, "tutorial changed; re-review"
    section = path.read_text().split("## Complete example", 1)[1]
    lines = [line.removeprefix("> ").removeprefix(">") for line in section.splitlines()]
    code = re.search(r"^\x60{3}python[^\n]*\n(.*?)^\x60{3}$", "\n".join(lines), re.M | re.S).group(
        1
    )
    nodes = [
        n
        for n in ast.parse(code).body
        if getattr(n, "name", None) in {"Skill", "load_skill", "SkillMiddleware"}
        or isinstance(n, ast.AnnAssign)
        and n.target.id == "SKILLS"
    ]
    assert len(nodes) == 4
    env = {
        "TypedDict": typing.TypedDict,
        "tool": tool,
        "AgentMiddleware": AgentMiddleware,
        "SystemMessage": SystemMessage,
        "ModelRequest": ModelRequest,
        "ModelResponse": ModelResponse,
        "Callable": typing.Callable,
    }
    exec(compile(ast.Module(nodes, type_ignores=[]), str(path), "exec"), env)
    return env


class Transport:
    def __init__(self, bridge):
        self.b = bridge
        self.inputs = []

    def invoke(self, messages, specs, role):
        data, system = [], []
        for m in messages:
            if m.type == "system":
                system.append(m.content)
            elif m.type == "human":
                data.append({"role": "user", "content": m.content})
            elif m.type == "ai":
                content = [{"type": "text", "text": m.content}] if m.content else []
                content += [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]}
                    for c in m.tool_calls
                ]
                data.append({"role": "assistant", "content": content})
            elif m.type == "tool":
                data.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
        tools = [
            {
                "name": s["function"]["name"],
                "input_schema": s["function"]["parameters"],
                "description": s["function"].get("description", ""),
            }
            for s in specs
        ]
        self.inputs.append({"system": system, "messages": data, "tools": tools})
        result = self.b.create(
            system=system, messages=data, tools=tools, model="tutorial-bridge", max_tokens=2000
        )
        return AIMessage(
            content="".join(b.text for b in result.content if b.type == "text"),
            tool_calls=[
                {"id": b.id, "name": b.name, "args": b.input}
                for b in result.content
                if b.type == "tool_use"
            ],
        )


def execute(sql, case, changed):
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(
            "CREATE TABLE customers(customer_id INT,name TEXT,email TEXT,signup_date TEXT,status TEXT,customer_tier TEXT);"  # noqa: E501 — fixed study text
            "CREATE TABLE products(product_id INT,product_name TEXT,sku TEXT,category TEXT,unit_cost INT,reorder_point INT,discontinued BOOL);"  # noqa: E501 — fixed study text
            "CREATE TABLE inventory(inventory_id INT,product_id INT,warehouse_id INT,quantity_on_hand INT,last_updated TEXT);"  # noqa: E501 — fixed study text
        )
        db.executemany(
            "INSERT INTO customers(customer_id,signup_date,status) VALUES(?,?,?)",
            [
                (11, "2026-05-01", "active"),
                (22, "2026-08-10", "active"),
                (33, "2026-08-11", "active"),
                (44, "2026-01-01", "active" if changed else "inactive"),
            ],
        )
        db.executemany(
            "INSERT INTO products(product_id,reorder_point,discontinued) VALUES(?,?,?)",
            [(11, 5, 0), (22, 5, 0), (33, 10, 1), (44, 10, 0)],
        )
        db.executemany(
            "INSERT INTO inventory(product_id,warehouse_id,quantity_on_hand) VALUES(?,?,?)",
            [
                (11, 1, 2),
                (11, 2, 4 if changed else 3),
                (22, 1, 3),
                (22, 2, 3),
                (33, 1, 1),
                (44, 1, 2),
            ],
        )
        expected = (
            ([11, 22, 44] if changed else [11, 22])
            if case == "sales"
            else ([44] if changed else [11, 44])
        )
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        db.set_authorizer(
            lambda op, *_: sqlite3.SQLITE_OK if op in allowed else sqlite3.SQLITE_DENY
        )
        db.set_progress_handler(lambda: 1, 100000)
        rows = db.execute(sql).fetchall()
        return {"passed": rows == [(i,) for i in expected], "actual": rows, "expected": expected}
    except sqlite3.Error as e:
        return {"passed": False, "error": str(e)}
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    env = original_parts(args.source)
    pack = {s["name"]: s for s in env["SKILLS"]}
    assert (
        env["load_skill"]
        .invoke({"skill_name": "not-a-skill"})
        .startswith("Skill 'not-a-skill' not found")
    )
    bridge = Bridge(args.output / "model", max_calls=16)
    questions = {
        "sales": "截至 2026-11-08，按公司的“活跃客户”定义，列出 customer_id 升序；只输出一条可执行 SQLite SELECT，无注释。",  # noqa: E501 — fixed study text
        "stock": "按公司的补货口径，列出需要补货的在售产品 product_id 升序；只输出一条可执行 SQLite SELECT，无注释。",  # noqa: E501 — fixed study text
    }
    base = (
        "You are a SQL query assistant that helps users write queries against business databases."
    )

    @tool
    def lookup_dictionary(domain: str) -> str:
        """Read database schema and business definitions. Valid domains: sales_analytics, inventory_management."""  # noqa: E501 — fixed study text
        return pack.get(domain, {}).get("content", "unknown domain")

    cells = [
        ("sales", "skill"),
        ("sales", "no_source"),
        ("sales", "dictionary"),
        ("stock", "dictionary"),
        ("stock", "no_source"),
        ("stock", "skill"),
    ]
    (args.output / "plan.json").write_text(
        json.dumps(
            {
                "cells": cells,
                "source_sha256": SHA256,
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "metric": "Generated SQL returns exactly the expected ordered IDs on both held-out SQLite fixtures. No correction/retries.",  # noqa: E501 — fixed study text
            }
        )
    )
    rows = []
    for case, mode in cells:
        t = Transport(bridge)
        start = bridge.calls
        graph = create_agent(
            CodexBridge(client=t),
            [] if mode != "dictionary" else [lookup_dictionary],
            system_prompt=base,
            middleware=[env["SkillMiddleware"]()] if mode == "skill" else [],
            checkpointer=InMemorySaver(),
        )
        row = {"case": case, "mode": mode, "passed": False}
        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": questions[case]}]},
                config={"configurable": {"thread_id": f"{case}-{mode}"}, "recursion_limit": 9},
            )
            sql = result["messages"][-1].content.strip()
            m = re.fullmatch(r"\x60{3}(?:sql|sqlite)?\s*(.*?)\s*\x60{3}", sql, re.I | re.S)
            if m:
                sql = m.group(1)
            checks = [execute(sql, case, changed) for changed in (False, True)]
            row.update(
                checks=checks,
                passed=all(c["passed"] for c in checks),
                calls_made=[
                    c["name"]
                    for msg in result["messages"]
                    if msg.type == "ai"
                    for c in msg.tool_calls
                ],
                initial_schema_absent=all(
                    s["content"] not in str(t.inputs[0]) for s in pack.values()
                ),
                retrieved_domains=[
                    k
                    for k, s in pack.items()
                    if any(
                        s["content"] in str(msg.content)
                        for msg in result["messages"]
                        if msg.type == "tool"
                    )
                ],
            )
            (args.output / f"{case}-{mode}.sql").write_text(sql)
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        row["model_calls"] = bridge.calls - start
        rows.append(row)
        (args.output / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(case, mode, row["passed"], row["model_calls"], flush=True)
        if (bridge.control / "STOP").exists():
            break


if __name__ == "__main__":
    main()
