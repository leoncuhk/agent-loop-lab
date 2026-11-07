import json

import pytest

from loop_lab.hard.tasks import audit_task, plan_task
from loop_lab.hard.world import HardWorld, calculate


def test_calculation_is_exact_and_cannot_execute_code():
    assert calculate("10000-10000*1000//10000+500-min(2000,9000*2000//10000)") == "7700"
    with pytest.raises(ValueError):
        calculate("__import__('os').system('whoami')")


def test_decimal_literals_keep_precision_and_deep_expressions_are_rejected():
    assert calculate("0.12345678901234567890+0") == "0.12345678901234567890"
    with pytest.raises(ValueError, match="deep"):
        calculate("+" * 1200 + "1")


def test_compact_projection_preserves_every_business_row(tmp_path):
    task = audit_task("A-test", 411)
    raw = HardWorld(task, tmp_path / "raw", "raw").read_packet("ledger.east")["data"]
    compact = HardWorld(task, tmp_path / "compact", "compact").read_packet("ledger.east")["data"]
    projected = [{k: v for k, v in row.items() if k != "observability"} for row in raw["rows"]]
    assert projected == compact["rows"]


def test_stale_revision_cannot_create_a_draft(tmp_path):
    task = plan_task("R-test", 311, revision=True)
    world = HardWorld(task, tmp_path, "compact")
    world.turn = 2
    result = world.stage_result(
        json.dumps(
            {
                "selected_ids": ["B1", "T1", "C1"],
                "cash_cents": 100,
                "utility": 100,
                "policy_version": "purchase-Q4-7",
                "request_revision": 0,
            }
        )
    )
    assert result["error"] == "stale_revision"
    assert world.proposals == []
