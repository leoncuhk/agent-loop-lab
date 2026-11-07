import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from loop_lab.fair.study import SETTINGS, report, run_study, run_trial
from loop_lab.hard.tasks import plan_task
from loop_lab.integrity import fingerprint


def cli_response(decision):
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(decision)}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
    ]
    return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in events), stderr="")


def test_timeout_is_preserved_and_recovered_with_remaining_trial_budget(monkeypatch, tmp_path):
    task = plan_task("dev", 211, delivery="quote")
    calls = []

    def process(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 240, output=b'{"type":"thread.started"}\n')
        request = json.loads(kwargs["input"])
        tools = [m for m in request["conversation"] if m["role"] == "tool"]
        if not tools:
            args = {
                "candidates": [["B2", "C6", "T2"]],
                "requirements": task["packets"]["case"]["requirements"],
                "request_revision": 0,
            }
            return cli_response(
                {
                    "final": None,
                    "tool_calls": [
                        {"name": "assess_candidates", "arguments_json": json.dumps(args)}
                    ],
                }
            )
        quote = json.loads(tools[-1]["content"])["best"]["quote_id"]
        return cli_response(
            {
                "final": None,
                "tool_calls": [
                    {"name": "stage_quote", "arguments_json": json.dumps({"quote_id": quote})}
                ],
            }
        )

    monkeypatch.setattr("loop_lab.models.subprocess.run", process)
    result = run_trial(
        task, "tools", 0, tmp_path / "trial", SETTINGS, threading.BoundedSemaphore(4), fingerprint()
    )
    assert result["first_passed"] is False and result["final_passed"] is True
    assert result["attempts"] == 2 and result["model_calls"] == 3
    assert result["unknown_usage_requests"] == 1
    assert (tmp_path / "trial/attempt-0/result.json").exists()
    assert (tmp_path / "trial/attempt-1/result.json").exists()


def test_business_failure_is_not_retried(monkeypatch, tmp_path):
    calls = []

    def process(cmd, **kwargs):
        calls.append(cmd)
        return cli_response(
            {"tool_calls": [], "final": {"status": "answered", "facts": [], "receipt_ids": []}}
        )

    monkeypatch.setattr("loop_lab.models.subprocess.run", process)
    result = run_trial(
        plan_task("dev", 211, delivery="quote"),
        "tools",
        0,
        tmp_path / "trial",
        SETTINGS,
        threading.BoundedSemaphore(4),
        fingerprint(),
    )
    assert result["attempts"] == 1 and len(calls) == 1
    assert result["first_passed"] is False


def test_report_does_not_call_duplicate_rows_a_balanced_comparison(tmp_path):
    manifest = {
        "phase": "test",
        "methods": ["tools", "skills"],
        "reps": 1,
        "blocks": [{"task_id": "T1", "rep": 0}],
        "planned_trials": 2,
        "source_unchanged": True,
    }
    row = {
        "task_id": "T1",
        "method": "tools",
        "rep": 0,
        "first_passed": True,
        "final_passed": True,
        "transport_fault": False,
        "model_calls": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "latency_s": 1,
        "actual_topology": "single",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "trials.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    assert report(tmp_path)["complete_balanced"] is False


def test_systemic_quota_failure_stops_after_current_balanced_block(monkeypatch, tmp_path):
    attempts = []

    def process(cmd, **kwargs):
        if cmd == ["codex", "login", "status"]:
            return SimpleNamespace(returncode=0, stdout="Logged in", stderr="")
        if cmd == ["codex", "--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-test", stderr="")
        attempts.append(cmd)
        event = {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}
        return SimpleNamespace(returncode=1, stdout=json.dumps(event), stderr="")

    monkeypatch.setattr("loop_lab.models.subprocess.run", process)
    output = tmp_path / "study"
    tasks = [plan_task(f"dev-{i}", 211 + i, delivery="quote") for i in range(3)]
    run_study(output, tasks, SETTINGS, methods=["tools", "eager"])
    manifest = json.loads((output / "manifest.json").read_text())
    summary = json.loads((output / "summary.json").read_text())
    assert len(attempts) == 2
    assert len(manifest["not_run"]) == 4
    assert manifest["stop_reason"] == "systemic_model_service:usage_limit"
    assert summary["complete_balanced"] is False


def test_integrity_failure_still_closes_manifest_and_lists_unstarted_trials(monkeypatch, tmp_path):
    monkeypatch.setattr("loop_lab.fair.study.ensure_authenticated", lambda: None)
    monkeypatch.setattr("loop_lab.fair.study.subprocess.check_output", lambda *a, **k: "codex-test")
    real = fingerprint()
    checks = []

    def changing_fingerprint():
        checks.append(True)
        return real if len(checks) <= 2 else {**real, "drift": "detected"}

    monkeypatch.setattr("loop_lab.fair.study.fingerprint", changing_fingerprint)
    output = tmp_path / "study"
    tasks = [plan_task("dev", 211, delivery="quote")]
    with pytest.raises(RuntimeError, match="Frozen source changed"):
        run_study(output, tasks, SETTINGS, methods=["tools"])
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["finished_at"]
    assert manifest["stop_reason"].startswith("study_error:")
    assert len(manifest["not_run"]) == 1
    assert (output / "REPORT.md").exists()
