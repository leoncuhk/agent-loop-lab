"""Explicit CLI entry points; real model use never happens on import."""

import argparse
import json
import os
from pathlib import Path

from loop_lab.experiment import run_batch
from loop_lab.models import Budget, ModelRun
from loop_lab.report import summarize
from loop_lab.runtime import Execution
from loop_lab.tasks import tasks


def main():
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tasks")
    report = sub.add_parser("report")
    report.add_argument("directory", type=Path)
    run = sub.add_parser("run", help="Run real-model trials using the logged-in Codex CLI")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--split", choices=["dev", "confirmation"], default="dev")
    run.add_argument(
        "--variants",
        nargs="+",
        choices=["multi", "tools", "skills", "eager"],
        default=["multi", "tools", "skills", "eager"],
    )
    run.add_argument("--tasks", nargs="+")
    run.add_argument("--reps", type=int, default=1)
    run.add_argument("--jobs", type=int, default=2)
    run.add_argument("--max-calls", type=int, default=160)
    run.add_argument("--max-tokens", type=int, default=1_800_000)
    run.add_argument("--model", default="gpt-6.1-sol")
    run.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    resume = sub.add_parser(
        "resume", help="Resume a retained approval using the same task and variant"
    )
    resume.add_argument("directory", type=Path)
    resume.add_argument("--decision", choices=["approve", "reject"], required=True)
    args = parser.parse_args()
    if args.command == "tasks":
        print(
            json.dumps(
                [{k: t[k] for k in ("id", "split", "group", "prompt")} for t in tasks()],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "report":
        print(json.dumps(summarize(args.directory)["variants"], ensure_ascii=False, indent=2))
    elif args.command == "resume":
        identity = json.loads((args.directory / "execution.json").read_text())
        client = ModelRun(
            args.directory / "model",
            Budget(max_calls=12),
            model=identity["model"],
            effort=identity["effort"],
            max_calls=identity["max_calls"],
        )
        execution = Execution(identity["task"], identity["variant"], client, args.directory)
        try:
            print(
                json.dumps(execution.advance(approval=args.decision), ensure_ascii=False, indent=2)
            )
        finally:
            execution.close()
    else:
        directory = run_batch(
            args.output,
            split=args.split,
            variants=args.variants,
            reps=args.reps,
            jobs=args.jobs,
            max_calls=args.max_calls,
            max_tokens=args.max_tokens,
            model=args.model,
            effort=args.effort,
            task_ids=args.tasks,
        )
        print(json.dumps(summarize(directory)["variants"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
