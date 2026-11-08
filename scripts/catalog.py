"""Generate the repository file index and validate tracked relative Markdown destinations."""

import argparse
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/files.md"
GROUPS = {
    "src": "三代运行代码",
    "skills": "客服实验用手册",
    "tests": "工程与边界回归",
    "analysis": "原始批次核验与分析",
    "scripts": "公开复核、资料导出与维护",
    "data": "公开逐次观测",
    "reports": "机器结果与修正",
    "reviews": "封存题、设计与证据审查",
    "fieldwork": "上游实验：代码、协议、结果",
    "docs": "阅读版与历史过程",
    ".github": "持续验证",
}


def tracked():
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return sorted(f for f in files if f)


def render(files):
    groups = defaultdict(list)
    for f in files:
        first = f.split("/", 1)[0]
        group = (
            "只读旧实现快照（不要在此开发）"
            if f.startswith("reviews/fair-v2-baseline/")
            else GROUPS.get(first, "根目录：入口、正式报告、协议与配置")
        )
        groups[group].append(f)
    lines = [
        "# 全部跟踪文件",
        "",
        "由 [catalog.py](../scripts/catalog.py) 从 Git 索引生成。角色与阅读顺序见"
        "[代码与资产地图](repository.md)。不含原始运行目录、缓存或外部代码。",
        "",
        f"本版共 **{len(files)}** 个文件；下面的数字是文件数，不是实验数或覆盖率。",
    ]
    for label, members in groups.items():
        lines += ["", f"## {label} · {len(members)}", ""]
        for name in members:
            link = os.path.relpath(ROOT / name, CATALOG.parent)
            lines.append(f"- [{name}]({link})")
    return "\n".join(lines) + "\n"


def check_links(files):
    known = set(files)
    dirs = {str(p) for f in files for p in Path(f).parents}
    broken = []
    for f in files:
        if not f.endswith(".md"):
            continue
        for url in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", (ROOT / f).read_text()):
            if re.match("^([a-z]+:|#|//)", url):
                continue
            p = unquote(url.split("#")[0])
            target = os.path.normpath(os.path.join(os.path.dirname(f), p))
            if target not in known and target not in dirs:
                broken.append((f, url))
    if broken:
        raise ValueError(f"Links not available from a public checkout: {broken}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate docs/files.md")
    args = ap.parse_args()
    names = tracked()
    if args.write:
        CATALOG.write_text(render(names))
    if render(names) != CATALOG.read_text():
        raise SystemExit("Outdated catalog: stage new paths and run scripts/catalog.py --write")
    check_links(names)
    print(f"Catalog and relative Markdown paths OK ({len(names)} tracked files)")
