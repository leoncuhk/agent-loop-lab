import json
import subprocess

from loop_lab.experiment import run_batch


def test_budget_exhaustion_retains_unstarted_trials_without_scoring_them(tmp_path, monkeypatch):
    # run_batch records CLI metadata even when the budget forbids every trial.
    # Stub only that probe; starting a real model would violate the test contract.
    original = subprocess.check_output

    def metadata_only(cmd, *args, **kwargs):
        if cmd == ["codex", "--version"]:
            return "offline-test-cli"
        if cmd and cmd[0] == "codex":
            raise AssertionError(f"Unexpected Codex invocation in offline test: {cmd[:2]}")
        return original(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "check_output", metadata_only)

    output = tmp_path / "empty-budget"
    run_batch(
        output,
        split="dev",
        variants=["tools", "skills"],
        reps=1,
        jobs=2,
        max_calls=0,
        max_tokens=100000,
        model="unused",
        effort="low",
        task_ids=["d01"],
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["actual_calls"] == 0
    assert len(manifest["not_run"]) == 2
    assert (output / "results.jsonl").read_text() == ""
