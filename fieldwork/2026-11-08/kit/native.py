"""Re-run upstream checks and re-compute upstream logged scores. All generated files go to RUN."""

import argparse
import hashlib
import json
import os
import platform
import runpy
import subprocess as sp
import sys
import tarfile
from pathlib import Path

SHA = "c43796287b83bec847db923f07131d1cc8de750d"
HERE = Path(__file__).resolve().parent
LAB = HERE.parents[2]
CLONE = (
    runpy.run_path(str(LAB / "scripts/upstreams.py"))["reference_root"]() / "single-agent-loop-kit"
)


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    src = out / "src"
    src.mkdir(exist_ok=False)
    tar = out / "upstream.tar"
    with tar.open("wb") as f:
        sp.run(["git", "-C", str(CLONE), "archive", SHA], stdout=f, check=True)
    with tarfile.open(tar) as f:
        f.extractall(src, filter="data")
    app = src / "examples/support-desk"
    scripts = src / "skill/scripts"
    logs = out / "native"
    logs.mkdir()
    native = {}

    def check(label, cwd, args, expect=(0,)):
        proc = sp.run(
            [sys.executable, *map(str, args)],
            cwd=cwd,
            text=True,
            stdout=sp.PIPE,
            stderr=sp.STDOUT,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "KIT_MODEL": "fake",
                "TMPDIR": str(out / "tmp"),
            },
        )
        (logs / (label + ".txt")).write_text(proc.stdout)
        native[label] = {
            "exit": proc.returncode,
            "expected_exit": list(expect),
            "argv": list(map(str, args)),
        }
        print(label, proc.returncode, flush=True)
        if proc.returncode not in expect:
            raise ValueError(f"{label}: {proc.stdout[-1600:]}")

    (out / "tmp").mkdir()
    check("tests", src, ["-m", "unittest", "discover", "tests"])
    check(
        "smoke-router",
        app,
        ["evals/run_eval.py", "--app", "app", "--variant", "smoke-router", "--out", out / "smoke"],
    )
    check(
        "smoke-finished",
        app,
        [
            "evals/run_eval.py",
            "--app",
            "single_loop",
            "--variant",
            "smoke-finished",
            "--out",
            out / "smoke",
        ],
    )
    traces = "traces/traces.jsonl"
    check("coupling", app, [scripts / "coupling_map.py", traces, "--exclude", "router", "--json"])
    stats = {}
    for label, flow, before, after in [
        ("fold", "example-run/results", "baseline", "v1"),
        ("as-tool", "example-run/as-tool/results", "baseline", "v1"),
        ("as-tool-extra", "example-run/as-tool/results", "baseline-t01-t10", "v1-t01-t10"),
    ]:
        check(
            "compare-" + label,
            app,
            [scripts / "compare.py", flow, before, after, "--exclude", "router"],
        )
        if label != "as-tool-extra":
            check(
                "coverage-" + label,
                app,
                [scripts / "chain_coverage.py", traces, flow, before, "--exclude", "router"],
                expect=((1,) if label == "fold" else (0,)),
            )
        stats[label] = {}
        for variant in [before, after]:
            rows = [
                json.loads(line)
                for line in (app / flow / variant / "results.jsonl").read_text().splitlines()
                if line
            ]
            stats[label][variant] = {
                "rows": len(rows),
                "passes": sum(next(iter(r["grade"].values())) == 1 for r in rows),
                "tasks": len(set(r["prompt_id"] for r in rows)),
                "input": sum(r["in_tokens"] for r in rows),
                "output": sum(r["out_tokens"] for r in rows),
                "calls": sum(r["model_calls"] for r in rows),
            }
    report = {
        "commit": SHA,
        "archive_sha256": hashlib.sha256(tar.read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ops": native,
        "published_rows": stats,
    }
    (out / "native-receipt.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    run(p.parse_args().run.resolve())
