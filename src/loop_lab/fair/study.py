"""Balanced trial blocks with isolated attempts and prespecified technical recovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from loop_lab.fair.knowledge import pack_hash
from loop_lab.fair.runtime import FairExecution
from loop_lab.fair.scoring import score_fair
from loop_lab.hard.tasks import audit_task, plan_task
from loop_lab.integrity import PROJECT, fingerprint
from loop_lab.models import Budget, ModelRun

METHODS = ["tools", "eager", "skills", "multi"]
SETTINGS = {
    "model": "gpt-6.1-sol",
    "effort": "low",
    "task_calls": 24,
    "request_timeout_s": 240,
    "known_token_threshold": 800000,
    "unknown_reservation": 150000,
    "reserve_main": 3,
    "worker_allowance": 6,
    "model_concurrency": 4,
    "technical_recoveries": 1,
}


def ensure_authenticated():
    status = subprocess.run(
        ["codex", "login", "status"], capture_output=True, text=True, timeout=20, check=False
    )
    if status.returncode != 0:
        raise RuntimeError("Codex CLI login unavailable; complete codex login before model trials")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def development_tasks():
    return [
        plan_task("DEV-P2", 211, delivery="quote"),
        plan_task("DEV-R2", 312, revision=True, delivery="quote"),
        audit_task("DEV-A0", 410, delivery="quote"),
    ]


def attempt(
    task,
    method,
    rep,
    index,
    directory,
    settings,
    remaining_calls,
    remaining_tokens,
    semaphore,
    frozen,
):
    directory.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    (directory / "attempt.json").write_text(
        json.dumps(
            {
                "status": "started",
                "index": index,
                "task": task["id"],
                "method": method,
                "rep": rep,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    budget = Budget(
        max_calls=remaining_calls,
        max_tokens=remaining_tokens,
        reserve_per_call=settings["unknown_reservation"],
    )
    adapter = ModelRun(
        directory / "model",
        budget,
        max_calls=remaining_calls,
        model=settings["model"],
        effort=settings["effort"],
        timeout=settings["request_timeout_s"],
    )
    execution = None
    error = None
    outcome = {
        "answer": None,
        "turn_answers": [],
        "world": {"events": [], "proposals": [], "records": {}},
        "actual_topology": "unknown",
        "delegation_spans": [],
    }
    try:
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source integrity changed")
        execution = FairExecution(
            task,
            method,
            adapter,
            directory,
            library=task.get("library", "small"),
            max_calls=remaining_calls,
            reserve_main=min(settings["reserve_main"], remaining_calls),
            worker_allowance=settings["worker_allowance"],
            semaphore=semaphore,
        )
        outcome = execution.run()
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source integrity changed")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if execution is not None:
            outcome["world"] = execution.world.export()
            outcome["turn_answers"] = execution.outputs
            outcome["delegation_spans"] = execution.delegation_spans
    finally:
        if execution is not None:
            execution.close()
    usage = [r.get("usage", {}) for r in adapter.records]
    unknown = sum(not bool(u) for u in usage)
    timeout = any(r.get("error") == "TimeoutExpired" for r in adapter.records)
    service_error = next(
        (r["service_error"] for r in adapter.records if r.get("service_error")), None
    )
    worker_roles = {r["role"] for r in adapter.records if r["role"].startswith("worker-")}
    outcome["actual_topology"] = "delegated" if worker_roles else "single"
    grade = score_fair(task, outcome["answer"], outcome["world"], error)
    result = {
        "task_id": task["id"],
        "family": task["family"],
        "method": method,
        "rep": rep,
        "attempt": index,
        "error": error,
        "transport_timeout": timeout,
        "service_error": service_error,
        "grade": grade,
        **outcome,
        "latency_s": time.monotonic() - start,
        "model_calls": adapter.calls,
        "worker_count": len(worker_roles),
        "main_calls": sum(r["role"] == "main" for r in adapter.records),
        "worker_calls": sum(r["role"].startswith("worker-") for r in adapter.records),
        "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
        "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
        "unknown_usage_requests": unknown,
        "cost_usd": None,
        "skills_loaded": [
            n
            for e in outcome["world"]["events"]
            if e["tool"] == "load_skills"
            for n in e["args"]["names"]
        ],
        "model_records": adapter.records,
        "library": task.get("library", "small"),
    }
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (directory / "attempt.json").write_text(
        json.dumps(
            {
                "status": "finished",
                "index": index,
                "finished_at": datetime.now(UTC).isoformat(),
                "error": error,
                "unknown_usage_requests": unknown,
            }
        )
    )
    return result


def run_trial(task, method, rep, directory, settings, semaphore, frozen):
    directory.mkdir(parents=True, exist_ok=False)
    rows = []
    calls_left = settings["task_calls"]
    tokens_left = settings["known_token_threshold"]
    for index in range(settings["technical_recoveries"] + 1):
        if calls_left <= 0 or tokens_left < settings["unknown_reservation"]:
            break
        row = attempt(
            task,
            method,
            rep,
            index,
            directory / f"attempt-{index}",
            settings,
            calls_left,
            tokens_left,
            semaphore,
            frozen,
        )
        rows.append(row)
        calls_left -= row["model_calls"]
        tokens_left -= row["input_tokens"] + row["output_tokens"]
        tokens_left -= row["unknown_usage_requests"] * settings["unknown_reservation"]
        if not row["transport_timeout"]:
            break
    first, final = rows[0], rows[-1]
    result = {
        "task_id": task["id"],
        "family": task["family"],
        "method": method,
        "rep": rep,
        "library": task.get("library", "small"),
        "first_passed": first["grade"]["passed"],
        "first_grade": first["grade"],
        "first_error": first["error"],
        "final_passed": final["grade"]["passed"],
        "final_grade": final["grade"],
        "final_error": final["error"],
        "attempts": len(rows),
        "model_calls": sum(r["model_calls"] for r in rows),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "latency_s": sum(r["latency_s"] for r in rows),
        "unknown_usage_requests": sum(r["unknown_usage_requests"] for r in rows),
        "transport_fault": any(r["transport_timeout"] for r in rows),
        "service_error": next((r["service_error"] for r in rows if r.get("service_error")), None),
        "actual_topology": final["actual_topology"],
        "worker_calls": sum(r["worker_calls"] for r in rows),
        "worker_count": final["worker_count"],
    }
    (directory / "trial.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_study(
    output, tasks, settings, *, reps=1, methods=METHODS, frozen=None, phase="development"
):
    ensure_authenticated()
    if (
        not tasks
        or not methods
        or len(methods) != len(set(methods))
        or reps < 1
        or len({t["id"] for t in tasks}) != len(tasks)
    ):
        raise ValueError("Require distinct methods/case IDs and positive repetitions")
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprint() if frozen is None else frozen
    if fingerprint() != frozen:
        raise ValueError("Source does not match freeze receipt")
    for relative in frozen:
        dest = output / "source-snapshot" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / relative, dest)
    blocks = [(task, rep) for rep in range(reps) for task in tasks]
    random.Random(20261107).shuffle(blocks)
    manifest = {
        "phase": phase,
        "settings": settings,
        "methods": methods,
        "reps": reps,
        "task_count": len(tasks),
        "planned_trials": len(tasks) * len(methods) * reps,
        "started_at": datetime.now(UTC).isoformat(),
        "fingerprint": frozen,
        "knowledge_hashes": {size: pack_hash(size) for size in ["small", "large"]},
        "cli": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "blocks": [{"task_id": t["id"], "rep": r} for t, r in blocks],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (output / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2))
    semaphore = threading.BoundedSemaphore(settings["model_concurrency"])
    rows = []
    stop_reason = None
    try:
        with (
            (output / "trials.jsonl").open("w") as log,
            ThreadPoolExecutor(max_workers=len(methods)) as pool,
        ):
            for number, (task, rep) in enumerate(blocks):
                if fingerprint() != frozen:
                    raise RuntimeError("Frozen source changed; study stopped")
                order = (
                    methods[(number + rep) % len(methods) :]
                    + methods[: (number + rep) % len(methods)]
                )
                futures = [
                    pool.submit(
                        run_trial,
                        task,
                        method,
                        rep,
                        output / f"{task['id']}-{method}-r{rep}",
                        settings,
                        semaphore,
                        frozen,
                    )
                    for method in order
                ]
                for future in as_completed(futures):
                    row = future.result()
                    rows.append(row)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                    print(
                        json.dumps(
                            {
                                k: row[k]
                                for k in [
                                    "task_id",
                                    "method",
                                    "rep",
                                    "first_passed",
                                    "final_passed",
                                    "model_calls",
                                    "attempts",
                                    "final_error",
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                service_errors = [r.get("service_error") for r in rows if r.get("service_error")]
                if service_errors:
                    stop_reason = "systemic_model_service:" + service_errors[0]
                    break
    except BaseException as exc:
        stop_reason = f"study_error:{type(exc).__name__}:{exc}"
        raise
    finally:
        manifest.update(
            finished_at=datetime.now(UTC).isoformat(),
            source_unchanged=fingerprint() == frozen,
            completed_trials=len(rows),
            actual_calls=sum(r["model_calls"] for r in rows),
            known_tokens=sum(r["input_tokens"] + r["output_tokens"] for r in rows),
            unknown_usage_requests=sum(r["unknown_usage_requests"] for r in rows),
            stop_reason=stop_reason,
            not_run=[
                {"task_id": t["id"], "method": m, "rep": r}
                for t, r in blocks
                for m in methods
                if not any(
                    row["task_id"] == t["id"] and row["method"] == m and row["rep"] == r
                    for row in rows
                )
            ],
        )
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        report(output)


def report(output):
    manifest = json.loads((output / "manifest.json").read_text())
    rows = [json.loads(s) for s in (output / "trials.jsonl").read_text().splitlines()]
    validity_path = output / "VALIDITY.json"
    validity = json.loads(validity_path.read_text()) if validity_path.exists() else {}
    summary = {}
    planned_per_method = len(manifest["blocks"])
    missing_delivery_flags = {
        "invalid_answer",
        "not_completed",
        "missing_final_draft",
        "receipt_mismatch",
    }
    for method in manifest["methods"]:
        items = [r for r in rows if r["method"] == method]
        if not items:
            continue
        summary[method] = {
            "trials": len(items),
            "assigned_trials": planned_per_method,
            "first_delivery": sum(
                not missing_delivery_flags.intersection(
                    r.get("first_grade", {}).get("failures", [])
                )
                for r in items
            ),
            "recovery_delivery": sum(
                not missing_delivery_flags.intersection(
                    r.get("final_grade", {}).get("failures", [])
                )
                for r in items
            ),
            "first_passed": sum(r["first_passed"] for r in items),
            "recovery_passed": sum(r["final_passed"] for r in items),
            "first_feasible": sum(r.get("first_grade", {}).get("feasible", False) for r in items),
            "first_optimal": sum(r.get("first_grade", {}).get("optimal", False) for r in items),
            "first_pass_rate_all_assigned": sum(r["first_passed"] for r in items)
            / planned_per_method,
            "recovery_pass_rate_all_assigned": sum(r["final_passed"] for r in items)
            / planned_per_method,
            "transport_faults": sum(r["transport_fault"] for r in items),
            "unknown_usage_requests": sum(r.get("unknown_usage_requests", 0) for r in items),
            "input_tokens_known": sum(r["input_tokens"] for r in items),
            "output_tokens_known": sum(r["output_tokens"] for r in items),
            "mean_calls": statistics.mean(r["model_calls"] for r in items),
            "mean_tokens_known": statistics.mean(
                r["input_tokens"] + r["output_tokens"] for r in items
            ),
            "mean_latency_s": statistics.mean(r["latency_s"] for r in items),
            "delegated_trials": sum(r["actual_topology"] == "delegated" for r in items),
        }
    expected = {
        (b["task_id"], method, b["rep"])
        for b in manifest["blocks"]
        for method in manifest["methods"]
    }
    counts = Counter((r["task_id"], r["method"], r["rep"]) for r in rows)
    missing = sorted(expected - set(counts))
    extras = sorted(set(counts) - expected)
    duplicates = sorted(k for k, n in counts.items() if n != 1)
    complete = (
        len(rows) == manifest["planned_trials"]
        and not missing
        and not extras
        and not duplicates
        and manifest.get("source_unchanged") is True
        and not manifest.get("stop_reason")
        and not any(r.get("service_error") for r in rows)
        and validity.get("eligible_for_architecture_comparison", True)
    )
    result = {
        "manifest": manifest,
        "complete_balanced": complete,
        "methods": summary,
        "missing": missing,
        "extras": extras,
        "duplicates": duplicates,
        "validity": validity,
    }
    result["paired_comparisons"] = []
    if complete:
        task_ids = sorted({r["task_id"] for r in rows})
        methods = manifest["methods"]
        for left_index, left in enumerate(methods):
            for right in methods[left_index + 1 :]:
                for metric in ("first_passed", "final_passed"):
                    diffs = []
                    flips = []
                    for task_id in task_ids:
                        a = [
                            r[metric]
                            for r in rows
                            if r["task_id"] == task_id and r["method"] == left
                        ]
                        b = [
                            r[metric]
                            for r in rows
                            if r["task_id"] == task_id and r["method"] == right
                        ]
                        diff = statistics.mean(b) - statistics.mean(a)
                        diffs.append(diff)
                        if diff:
                            flips.append(
                                {
                                    "task": task_id,
                                    "left_passes": sum(a),
                                    "right_passes": sum(b),
                                    "reps": len(a),
                                }
                            )
                    rng = random.Random(20261107)
                    boot = sorted(
                        statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(4000)
                    )
                    result["paired_comparisons"].append(
                        {
                            "left": left,
                            "right": right,
                            "metric": metric,
                            "task_count": len(task_ids),
                            "right_minus_left": statistics.mean(diffs),
                            "task_cluster_ci95": [boot[99], boot[3899]],
                            "changed_tasks": flips,
                        }
                    )
    result["by_task"] = {
        task: {
            method: [r for r in rows if r["task_id"] == task and r["method"] == method]
            for method in manifest["methods"]
        }
        for task in sorted({r["task_id"] for r in rows})
    }
    (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    lines = [
        "# 公平对照结果",
        "",
        f"阶段：{manifest['phase']}；完整平衡：{complete}。",
        "",
        "| 方法 | 运行 | 首次通过 | 公共恢复后通过 | 技术故障 | 平均请求 | "
        "已知平均tokens | 平均秒 | 实际委派任务 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method, v in summary.items():
        lines.append(
            f"| {method} | {v['trials']} | {v['first_passed']} | {v['recovery_passed']} | "
            f"{v['transport_faults']} | {v['mean_calls']:.2f} | {v['mean_tokens_known']:.0f} | "
            f"{v['mean_latency_s']:.2f} | {v['delegated_trials']} |"
        )
    lines += ["", "## 未通过的首次结果", ""]
    if not complete:
        lines.append(
            "本批次不支持架构排名："
            + str(validity.get("reason") or manifest.get("stop_reason") or "配对覆盖不完整")
        )
    for r in rows:
        if not r["first_passed"]:
            lines.append(
                f"- {r['task_id']}/{r['method']}/r{r['rep']}: {r['first_grade']['failures']}；"
                f"恢复后通过={r['final_passed']}；error={r['first_error']}"
            )
    lines += [
        "",
        "## 配对比较与分母",
        "",
        "主成功率使用全部预分配trial作分母，未运行单列为未交付，不能解释成已观察的语义错误。",
        "summary.json另外记录首次/恢复交付、可行、最优、分开输入输出、未知用量与逐题结果。",
    ]
    for comparison in result["paired_comparisons"]:
        if comparison["metric"] == "first_passed":
            lines.append(
                f"- {comparison['right']} − {comparison['left']}："
                f"{comparison['right_minus_left']:+.3f}；按任务聚类bootstrap 95%区间 "
                f"{comparison['task_cluster_ci95']}。"
            )
    lines += [
        "",
        "这些区间仅描述固定任务集的聚类重采样，不包含任务生成、调优和领域外推不确定性；"
        "没有多重比较校正，全通过的零宽区间不证明真实差异为零。",
        "未知用量保留，不当作零；同请求/effort上限不等于相同实际算力。",
        "耗时包含CLI启动与四组共享并发限制的排队，不能直接推广为独立部署速度。",
        "按分配方法保留所有结果；multi未委派的成功不属于多上下文机制证据。",
        "同库eager/skills正文hash相同。合成任务、单模型和CLI桥接不支持普遍架构排名。",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n")
    return result


def freeze(path, sealed, development):
    if path.exists():
        raise ValueError("Freeze receipt already exists")
    dev = json.loads((development / "summary.json").read_text())
    if (
        not dev["complete_balanced"]
        or dev["manifest"]["phase"] != "development"
        or dev["manifest"]["methods"] != METHODS
        or dev["manifest"]["settings"] != SETTINGS
        or dev["manifest"]["fingerprint"] != fingerprint()
        or dev["manifest"]["task_count"] != 3
    ):
        raise ValueError("Freeze requires a complete, current-code, four-method development run")
    receipt = {
        "created_at": datetime.now(UTC).isoformat(),
        "fingerprint": fingerprint(),
        "sealed_tasks_path": str(sealed.resolve()),
        "sealed_sha256": sha(sealed),
        "settings": SETTINGS,
        "methods": METHODS,
        "reps": 2,
        "library_assignment": "alternating small/large in sealed task order; "
        "identical for every method",
        "knowledge_hashes": {size: pack_hash(size) for size in ["small", "large"]},
        "development_manifest_path": str((development / "manifest.json").resolve()),
        "development_manifest_sha256": sha(development / "manifest.json"),
        "development_trials_sha256": sha(development / "trials.jsonl"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


def main():
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["dev", "freeze", "test", "report"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--development", type=Path)
    parser.add_argument(
        "--sealed", type=Path, default=Path("reviews/fair-v2-holdout-tasks.sealed.json")
    )
    args = parser.parse_args()
    if args.mode == "dev":
        run_study(args.output, development_tasks(), SETTINGS, phase="development")
    elif args.mode == "freeze":
        if args.development is None:
            parser.error("freeze requires --development with the selected development run")
        freeze(args.output, args.sealed, args.development)
        print("Frozen source, knowledge, resource policy and sealed dataset hash")
    elif args.mode == "test":
        if args.receipt is None:
            parser.error("test requires --receipt")
        receipt = json.loads(args.receipt.read_text())
        if fingerprint() != receipt["fingerprint"] or sha(args.sealed) != receipt["sealed_sha256"]:
            raise ValueError("Frozen code or sealed dataset changed")
        tasks = json.loads(args.sealed.read_text())["tasks"]
        for i, task in enumerate(tasks):
            task["library"] = "large" if i % 2 else "small"
        run_study(
            args.output,
            tasks,
            receipt["settings"],
            reps=receipt["reps"],
            methods=receipt["methods"],
            frozen=receipt["fingerprint"],
            phase="sealed-holdout",
        )
    else:
        print(json.dumps(report(args.output)["methods"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
