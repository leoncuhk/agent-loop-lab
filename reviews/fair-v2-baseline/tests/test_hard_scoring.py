import json

from loop_lab.hard.scoring import score_hard
from loop_lab.hard.tasks import plan_task
from loop_lab.hard.world import HardWorld


def test_correct_selection_with_false_cash_claim_is_not_accepted(tmp_path):
    task = plan_task("P-test", 111)
    world = HardWorld(task, tmp_path, "compact")
    for packet in task["required_packets"]:
        world.read_packet(packet)
    best = task["expected"]
    payload = {k: best[k] for k in ["selected_ids", "cash_cents", "utility"]}
    payload.update(policy_version=task["policy"]["version"], request_revision=0)
    payload["cash_cents"] += 1
    staged = world.stage_result(json.dumps(payload))
    answer = {
        "status": "completed",
        "facts": staged["facts"],
        "receipt_ids": [staged["receipt_id"]],
    }
    result = score_hard(task, answer, world.export())
    assert result["feasible"] is True
    assert result["optimal"] is True
    assert result["passed"] is False
    assert "misreported_cash" in result["failures"]


def test_prior_revision_submission_remains_a_failure(tmp_path):
    task = plan_task("R-test", 311, revision=True)
    world = HardWorld(task, tmp_path, "compact")
    for packet in task["required_packets"]:
        world.read_packet(packet)
    best = task["expected"]
    payload = {k: best[k] for k in ["selected_ids", "cash_cents", "utility"]}
    payload.update(policy_version=task["policy"]["version"], request_revision=0)
    world.stage_result(json.dumps(payload))
    world.turn = 2
    payload["request_revision"] = 2
    staged = world.stage_result(json.dumps(payload))
    answer = {
        "status": "completed",
        "facts": staged["facts"],
        "receipt_ids": [staged["receipt_id"]],
    }
    result = score_hard(task, answer, world.export())
    assert result["passed"] is False
    assert "premature_submission" in result["failures"]


def test_reading_evidence_after_submission_does_not_ground_the_decision(tmp_path):
    task = plan_task("P-test", 111)
    world = HardWorld(task, tmp_path, "compact")
    best = task["expected"]
    payload = {k: best[k] for k in ["selected_ids", "cash_cents", "utility"]}
    payload.update(policy_version=task["policy"]["version"], request_revision=0)
    staged = world.stage_result(json.dumps(payload))
    for packet in task["required_packets"]:
        world.read_packet(packet)
    answer = {
        "status": "completed",
        "facts": staged["facts"],
        "receipt_ids": [staged["receipt_id"]],
    }
    assert "evidence_after_submission" in score_hard(task, answer, world.export())["failures"]
