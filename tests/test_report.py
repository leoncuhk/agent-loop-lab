import json

from loop_lab.report import summarize


def test_known_apparatus_defect_blocks_comparison_without_dropping_rows(tmp_path):
    manifest = {
        "planned_trials": 2,
        "source_unchanged": True,
        "model": "fixed",
        "effort": "low",
        "split": "confirmation",
        "reps": 1,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    rows = [
        {
            "variant": name,
            "task_id": "h01",
            "grade": {"passed": True, "failures": []},
            "error": None,
            "latency_s": 1,
            "model_calls": 1,
            "input_tokens": 10,
            "output_tokens": 2,
            "skills_loaded": [],
            "usage_complete": True,
        }
        for name in ("tools", "skills")
    ]
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (tmp_path / "VALIDITY.json").write_text(
        json.dumps(
            {"eligible_for_architecture_comparison": False, "reason": "known apparatus failure"}
        )
    )
    result = summarize(tmp_path)
    assert result["comparison_valid"] is False
    assert result["completed_trials"] == 2
    assert result["comparisons"] == []
