"""Create a human-readable read-only snapshot of the active fair study."""

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.directory
    manifest = json.loads((root / "manifest.json").read_text())
    rows = [json.loads(s) for s in (root / "trials.jsonl").read_text().splitlines() if s]
    active = []
    for path in sorted(root.glob("*/attempt-*")):
        if not (path / "result.json").exists():
            usage = path / "model/usage.jsonl"
            calls = len(usage.read_text().splitlines()) if usage.exists() else 0
            active.append(f"{path.parent.name}（已返回 {calls} 次模型调用）")
    counts = Counter(r["method"] for r in rows)
    passes = Counter(r["method"] for r in rows if r["first_passed"])
    state = "已结束" if manifest.get("finished_at") else "运行中"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# 四方法公平实测进度",
        "",
        f"快照时间：{now}。状态：**{state}**。",
        "",
        f"完成 **{len(rows)}/{manifest['planned_trials']}** 次分配运行。"
        "部分结果的题目/重复覆盖不完整，不能据此比较总排名。",
        "",
        "| 方法 | 已完成 | 首次契约通过 |",
        "|---|---:|---:|",
    ]
    for method in manifest["methods"]:
        lines.append(f"| {method} | {counts[method]} | {passes[method]} |")
    lines += [
        "",
        "当前在途：" + ("；".join(active) if active else "无"),
        "",
        "技术恢复次数：" + str(sum(r["attempts"] - 1 for r in rows)) + "。",
        "多 Agent 已完成项中的实际委派次数（按trial）："
        + str(sum(r["actual_topology"] == "delegated" for r in rows if r["method"] == "multi"))
        + "。",
        "",
        "开发阶段已完成两轮，每轮四组各3/3。这里只显示冻结新题；最终结论以完整报告为准。",
    ]
    text = "\n".join(lines) + "\n"
    if args.output:
        pending = args.output.with_suffix(".pending")
        pending.write_text(text)
        pending.replace(args.output)
    print(text)


if __name__ == "__main__":
    main()
