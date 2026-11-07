"""Frozen, interleaved trials. Failed runs remain in the denominator."""

from __future__ import annotations

import json
import platform
import random
import shutil
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from loop_lab.integrity import PROJECT, fingerprint
from loop_lab.models import Budget, ModelRun
from loop_lab.runtime import Execution
from loop_lab.schema import PROTOCOL
from loop_lab.scoring import score
from loop_lab.tasks import tasks


def run_one(task, variant, rep, directory, budget, frozen, model, effort):
    directory.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    client = ModelRun(directory / "model", budget, model=model, effort=effort)
    execution = None
    error = None
    outcome = {"answer": None, "world": {"events": [], "receipts": [], "pending": []}}
    try:
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source changed before trial")
        execution = Execution(task, variant, client, directory)
        outcome = execution.advance()
        for _ in range(4):
            if not outcome["pending"] or task["approval"] == "pause":
                break
            outcome = execution.advance(approval=task["approval"])
        if fingerprint() != frozen:
            raise RuntimeError("Frozen source changed during trial")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if execution is not None:
            outcome["world"] = execution.world.export()
    finally:
        if execution is not None:
            execution.close()
    uses = [r.get("usage", {}) for r in client.records]
    row = {
        "task_id": task["id"],
        "split": task["split"],
        "group": task["group"],
        "variant": variant,
        "rep": rep,
        "error": error,
        "grade": score(task["expected"], outcome["answer"], outcome["world"], error),
        "answer": outcome["answer"],
        "world": outcome["world"],
        "latency_s": time.monotonic() - start,
        "model_calls": client.calls,
        "input_tokens": sum(u.get("input_tokens", 0) for u in uses),
        "output_tokens": sum(u.get("output_tokens", 0) for u in uses),
        "cached_input_tokens": sum(u.get("cached_input_tokens", 0) for u in uses),
        "usage_complete": all(bool(r.get("usage")) for r in client.records),
        "cost_usd": None,
        "skills_loaded": [
            e["args"]["name"] for e in outcome["world"]["events"] if e["tool"] == "load_skill"
        ],
        "delegations": [
            e["args"]["specialist"] for e in outcome["world"]["events"] if e["tool"] == "delegate"
        ],
        "model_records": client.records,
    }
    (directory / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=2))
    return row


def run_batch(
    output: Path,
    *,
    split: str,
    variants: list[str],
    reps: int,
    jobs: int,
    max_calls: int,
    max_tokens: int,
    model: str,
    effort: str,
    task_ids: list[str] | None = None,
    task_set: list[dict] | None = None,
    trial_runner=None,
    metadata: dict | None = None,
    reserve_per_call: int = 50_000,
    snapshot_source: bool = False,
) -> Path:
    available = tasks(split) if task_set is None else task_set
    selected = [t for t in available if not task_ids or t["id"] in task_ids]
    if not selected or reps < 1 or jobs < 1 or not variants:
        raise ValueError("Require tasks, variants, positive repetitions and jobs")
    if len(set(variants)) != len(variants):
        raise ValueError("Variants must be distinct")
    output.mkdir(parents=True, exist_ok=False)
    frozen = fingerprint()
    if snapshot_source:
        for relative in frozen:
            destination = output / "source-snapshot" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT / relative, destination)
    budget = Budget(max_calls=max_calls, max_tokens=max_tokens, reserve_per_call=reserve_per_call)
    trial_runner = run_one if trial_runner is None else trial_runner
    schedule = []
    for rep in range(reps):
        for number, task in enumerate(selected):
            order = (
                variants[(number + rep) % len(variants) :]
                + variants[: (number + rep) % len(variants)]
            )
            schedule.extend((task, v, rep) for v in order)
    # Randomize case blocks, preserving a rotating order within each block.
    blocks = [schedule[i : i + len(variants)] for i in range(0, len(schedule), len(variants))]
    random.Random(20261106).shuffle(blocks)
    schedule = [trial for block in blocks for trial in block]
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "protocol": PROTOCOL,
        "model": model,
        "effort": effort,
        "variants": variants,
        "split": split,
        "reps": reps,
        "jobs": jobs,
        "max_calls": max_calls,
        "max_tokens": max_tokens,
        "task_count": len(selected),
        "planned_trials": len(schedule),
        "fingerprint": frozen,
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("langchain", "langgraph", "langchain-core")},
        "cli": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "schedule": [{"task": t["id"], "variant": v, "rep": r} for t, v, r in schedule],
        "limitations": [
            "Synthetic author-created tasks; not independent business evidence",
            "Same CLI structured-decision bridge for all arms; not native API latency",
            "CLI subscription usage has no verified dollar price",
            "Confirmation cases reserved until freeze, but authored in this project",
        ],
        "suite_metadata": metadata or {},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (output / "tasks.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2))
    start = time.monotonic()
    next_trial = 0
    not_run = []
    budget_interrupted = []
    with (
        ThreadPoolExecutor(max_workers=jobs) as pool,
        (output / "results.jsonl").open("w") as stream,
    ):
        futures = set()
        while futures or next_trial < len(schedule):
            while (
                len(futures) < min(jobs, budget.trial_slots())
                and next_trial < len(schedule)
                and budget.can_dispatch()
            ):
                task, variant, rep = schedule[next_trial]
                next_trial += 1
                futures.add(
                    pool.submit(
                        trial_runner,
                        task,
                        variant,
                        rep,
                        output / f"{task['id']}-{variant}-{rep}",
                        budget,
                        frozen,
                        model,
                        effort,
                    )
                )
            if not futures:
                break
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                row = future.result()
                label = {"task": row["task_id"], "variant": row["variant"], "rep": row["rep"]}
                if row["error"] and row["error"].startswith("SharedBudgetExceeded"):
                    if row["model_calls"] == 0:
                        not_run.append(label)
                        continue
                    budget_interrupted.append(label)
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                print(
                    json.dumps(
                        {
                            k: row[k]
                            for k in (
                                "task_id",
                                "variant",
                                "rep",
                                "grade",
                                "model_calls",
                                "latency_s",
                                "error",
                            )
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    not_run.extend({"task": t["id"], "variant": v, "rep": r} for t, v, r in schedule[next_trial:])
    manifest.update(
        {
            "finished_at": datetime.now(UTC).isoformat(),
            "wall_time_s": time.monotonic() - start,
            "actual_calls": budget.calls,
            "actual_tokens": budget.tokens,
            "not_run": not_run,
            "budget_interrupted": budget_interrupted,
            "usage_unknown": budget.unknown_usage,
            "token_reservation_per_call": budget.reserve_per_call,
            "source_unchanged": fingerprint() == frozen,
        }
    )
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return output
