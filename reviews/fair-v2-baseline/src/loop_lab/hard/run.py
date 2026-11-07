"""Run declared hard-task cells with the same real-model transport and shared budget."""

import argparse
import json
import os
import threading
import time
from collections import defaultdict
from functools import partial
from pathlib import Path

from loop_lab.experiment import run_batch
from loop_lab.hard.knowledge import skill_library
from loop_lab.hard.runtime import HardExecution, ThrottledModelRun
from loop_lab.hard.scoring import score_hard
from loop_lab.hard.tasks import hard_tasks
from loop_lab.integrity import fingerprint
from loop_lab.report import summarize


def trial(
    task,
    variant,
    rep,
    directory,
    budget,
    frozen,
    model,
    effort,
    *,
    library,
    representation,
    semaphore,
    task_budget,
):
    directory.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    client = ThrottledModelRun(
        directory / "model",
        budget,
        max_calls=task_budget,
        model=model,
        effort=effort,
        semaphore=semaphore,
    )
    execution = None
    error = None
    outcome = {"answer": None, "turn_answers": [], "world": {"events": [], "proposals": []}}
    try:
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source changed before hard trial")
        execution = HardExecution(
            task, variant, client, directory, library=library, representation=representation
        )
        outcome = execution.run()
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source changed during hard trial")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if execution:
            outcome["world"] = execution.world.export()
            outcome["turn_answers"] = execution.outputs
    finally:
        if execution:
            execution.close()
    usages = [r.get("usage", {}) for r in client.records]
    row = {
        "task_id": task["id"],
        "split": task["split"],
        "group": task["family"],
        "variant": variant,
        "rep": rep,
        "error": error,
        "grade": score_hard(task, outcome["answer"], outcome["world"], error),
        **outcome,
        "latency_s": time.monotonic() - start,
        "model_calls": client.calls,
        "input_tokens": sum(u.get("input_tokens", 0) for u in usages),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
        "cached_input_tokens": sum(u.get("cached_input_tokens", 0) for u in usages),
        "usage_complete": all(bool(r.get("usage")) for r in client.records),
        "cost_usd": None,
        "skills_loaded": [
            e["args"]["name"] for e in outcome["world"]["events"] if e["tool"] == "load_skill"
        ],
        "delegations": [
            e["args"]["specialist"] for e in outcome["world"]["events"] if e["tool"] == "delegate"
        ],
        "model_records": client.records,
        "library": library,
        "representation": representation,
    }
    (directory / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2))
    return row


def detail_report(directory):
    summary = summarize(directory)
    rows = [json.loads(s) for s in (directory / "results.jsonl").read_text().splitlines()]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["group"], row["variant"])].append(row)
    lines = [
        "# 困难任务分项结果",
        "",
        "可行/正确、最优与完整交付分别计数；运行错误仍保留。",
        "",
        "| 任务类 | 方案 | 运行 | 完整通过 | 可行/审计正确 | 最优 | 平均调用 | 平均秒 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for (family, variant), items in sorted(groups.items()):
        n = len(items)
        lines.append(
            f"| {family} | {variant} | {n} | {sum(r['grade']['passed'] for r in items)} | "
            f"{sum(r['grade']['feasible'] for r in items)} | "
            f"{sum(r['grade']['optimal'] for r in items)} | "
            f"{sum(r['model_calls'] for r in items) / n:.2f} | "
            f"{sum(r['latency_s'] for r in items) / n:.2f} |"
        )
    lines += ["", "## 失败机制", ""]
    for row in rows:
        if not row["grade"]["passed"]:
            lines.append(
                f"- {row['task_id']}/{row['variant']}/{row['rep']}: "
                f"{row['grade']['failures']}；utility gap={row['grade']['utility_gap']}；"
                f"audit accuracy={row['grade']['audit_branch_accuracy']}；error={row['error']}"
            )
    lines += [
        "",
        f"完整可比较：{summary['comparison_valid']}。manifest记录库规模、材料表示和模型并发。",
        "合成任务和作者oracle；不把组内小样本或CLI耗时当作业务可靠性保证。",
    ]
    (directory / "HARD-REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def main():
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", nargs="+", default=["P0"])
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["tools", "eager", "skills", "domain", "context", "audit_single"],
        default=["tools"],
    )
    parser.add_argument("--library", choices=["small", "large"], default="small")
    parser.add_argument("--representation", choices=["raw", "compact"], default="compact")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--model-concurrency", type=int, default=4)
    parser.add_argument("--task-budget", type=int, default=24)
    parser.add_argument("--max-calls", type=int, default=200)
    parser.add_argument("--max-tokens", type=int, default=4000000)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.model_concurrency <= 4:
        parser.error("--model-concurrency must be between 1 and 4")
    if not 1 <= args.task_budget <= 24:
        parser.error("--task-budget must be between 1 and 24")
    if args.report:
        print(json.dumps(detail_report(args.report)["variants"], indent=2))
        return
    if not args.output:
        parser.error("--output is required for a real model run")
    available = hard_tasks()
    selected = [{**t, "split": "hard-study"} for t in available if t["id"] in args.cases]
    if {t["id"] for t in selected} != set(args.cases):
        parser.error("Unknown case ID")
    if {"context", "audit_single"}.intersection(args.variants) and any(
        t["family"] != "audit" for t in selected
    ):
        parser.error("context arm is only defined for audits")
    semaphore = threading.BoundedSemaphore(args.model_concurrency)
    runner = partial(
        trial,
        library=args.library,
        representation=args.representation,
        semaphore=semaphore,
        task_budget=args.task_budget,
    )
    library = skill_library(args.library)
    metadata = {
        "study": "hard-v1",
        "library": args.library,
        "skill_count": len(library),
        "skill_body_characters": sum(len(v["body"]) for v in library.values()),
        "representation": args.representation,
        "per_task_calls": args.task_budget,
        "max_parallel_model_calls": args.model_concurrency,
        "admissible_claim": "Synthetic frozen oracle study; CLI bridge; no production effects",
    }
    run_batch(
        args.output,
        split="hard-study",
        variants=args.variants,
        reps=args.reps,
        jobs=args.jobs,
        max_calls=args.max_calls,
        max_tokens=args.max_tokens,
        model=args.model,
        effort=args.effort,
        task_set=selected,
        trial_runner=runner,
        metadata=metadata,
        reserve_per_call=150000,
        snapshot_source=True,
    )
    print(json.dumps(detail_report(args.output)["variants"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
