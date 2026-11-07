"""Transparent summaries and paired task-cluster uncertainty."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path


def summarize(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    validity_path = directory / "VALIDITY.json"
    validity = json.loads(validity_path.read_text()) if validity_path.exists() else {}
    rows = [json.loads(s) for s in (directory / "results.jsonl").read_text().splitlines()]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["variant"]].append(row)
    summary = {}
    for name, runs in grouped.items():
        summary[name] = {
            "trials": len(runs),
            "passed": sum(r["grade"]["passed"] for r in runs),
            "runtime_errors": sum(r["error"] is not None for r in runs),
            "mean_latency_s": statistics.mean(r["latency_s"] for r in runs),
            "mean_calls": statistics.mean(r["model_calls"] for r in runs),
            "mean_input_tokens": statistics.mean(r["input_tokens"] for r in runs),
            "mean_output_tokens": statistics.mean(r["output_tokens"] for r in runs),
            "loaded_skill_trials": sum(bool(r["skills_loaded"]) for r in runs),
            "usage_complete": all(r["usage_complete"] for r in runs),
        }
    by_task = defaultdict(dict)
    for name, runs in grouped.items():
        for task in {r["task_id"] for r in runs}:
            matches = [r for r in runs if r["task_id"] == task]
            by_task[task][name] = {
                "passed": sum(r["grade"]["passed"] for r in matches),
                "trials": len(matches),
                "failures": sorted({f for r in matches for f in r["grade"]["failures"]}),
            }
    comparisons = []
    comparison_valid = (
        len(rows) == manifest["planned_trials"]
        and not manifest.get("not_run")
        and not manifest.get("budget_interrupted")
        and not manifest.get("usage_unknown")
        and manifest.get("source_unchanged") is True
        and validity.get("eligible_for_architecture_comparison", True)
    )
    names = sorted(grouped)
    for i, left in enumerate(names if comparison_valid else []):
        for right in names[i + 1 :]:
            diffs = []
            worse, better = [], []
            for task, results in sorted(by_task.items()):
                if left not in results or right not in results:
                    continue
                a, b = results[left], results[right]
                diff = b["passed"] / b["trials"] - a["passed"] / a["trials"]
                diffs.append(diff)
                if diff < 0:
                    worse.append(task)
                if diff > 0:
                    better.append(task)
            rng = random.Random(20261106)
            boot = (
                sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(2000))
                if diffs
                else []
            )
            comparisons.append(
                {
                    "left": left,
                    "right": right,
                    "tasks": len(diffs),
                    "difference": statistics.mean(diffs) if diffs else None,
                    "cluster_bootstrap_95": [boot[49], boot[1949]] if boot else None,
                    "observed_worse": worse,
                    "observed_better": better,
                }
            )
    output = {
        "manifest": manifest,
        "variants": summary,
        "by_task": dict(by_task),
        "comparisons": comparisons,
        "completed_trials": len(rows),
        "comparison_valid": comparison_valid,
        "validity": validity,
    }
    (directory / "summary.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    lines = [
        "# Agent Loop 实验结果",
        "",
        f"模型：{manifest['model']}；"
        f"effort={manifest['effort']}；split={manifest['split']}；reps={manifest['reps']}。",
        "",
        f"完成 {len(rows)}/{manifest['planned_trials']} 次运行；源码未变："
        f"{manifest.get('source_unchanged', '尚未结束')}。",
        "",
        "| 方案 | 通过/运行 | 错误 | 平均秒 | 平均模型调用 | 平均输入/输出 tokens |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, v in sorted(summary.items()):
        lines.append(
            f"| {name} | {v['passed']}/{v['trials']} | {v['runtime_errors']} | "
            f"{v['mean_latency_s']:.2f} | {v['mean_calls']:.2f} | "
            f"{v['mean_input_tokens']:.0f}/{v['mean_output_tokens']:.0f} |"
        )
    lines += [
        "",
        "## 逐题结果",
        "",
        "| 任务 | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for task, result in sorted(by_task.items()):
        cells = []
        for name in names:
            r = result.get(name)
            cells.append(
                f"{r['passed']}/{r['trials']}"
                + ("；" + ", ".join(r["failures"]) if r["failures"] else "")
                if r
                else "未运行"
            )
        lines.append("| " + task + " | " + " | ".join(cells) + " |")
    lines += ["", "## 对照与边界", ""]
    if not comparison_valid:
        lines.append("本批次不满足完整可比条件，不计算架构优劣差异。未运行与预算中断见manifest。")
        if validity.get("reason"):
            lines.append("装置/解释边界：" + validity["reason"])
    if manifest.get("not_run"):
        lines.append(f"未运行 {len(manifest['not_run'])} 项（未混入已运行任务成功率）。")
    for c in comparisons:
        lines.append(
            f"- {c['right']} − {c['left']}：任务平均成功率差 "
            f"{c['difference']:+.3f}，按任务聚类 bootstrap 95% 区间 "
            f"{c['cluster_bootstrap_95']}；观察到下降 {c['observed_worse']}，"
            f"上升 {c['observed_better']}。"
        )
    lines += [
        "",
        "该区间只描述这批固定合成任务的重采样差异，不包含任务生成、模型变动、"
        "调优选择和业务外推不确定性。全通过时退化区间不证明真实差异为零。",
        "",
        "CLI 启动/认证/默认运行上下文开销包含在计量中；金额未知，未记录为零成本。"
        "状态、结构化事实与动作回执通过不等于自然语言质量或真实客户效果。",
        "",
        "manifest.json 绑定代码、Skills、任务与环境；每个任务目录保存请求、响应、工具"
        "事件、SQLite回执及checkpoint。错误保持在分母。",
    ]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n")
    return output
