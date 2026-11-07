from loop_lab.fair.scoring import score_fair
from loop_lab.fair.world import DecisionWorld


def test_same_total_from_wrong_event_version_is_rejected(tmp_path):
    rows = [
        {
            "kind": "posting",
            "id": "old",
            "ref": "I1",
            "seq": 1,
            "at": 1,
            "state": "approved",
            "amount": 100,
        },
        {
            "kind": "posting",
            "id": "new",
            "ref": "I1",
            "seq": 2,
            "at": 2,
            "state": "approved",
            "amount": 100,
        },
    ]
    policy = {"version": "audit-v", "cutoff": 10, "flag_threshold": 1000}
    task = {
        "id": "audit-test",
        "family": "audit",
        "messages": ["提交审计"],
        "packets": {
            "case": {
                "branches": ["east"],
                "policy_ref": "policy.audit",
                "ledger_refs": {"east": "ledger.east"},
            },
            "policy.audit": policy,
            "ledger.east": {"rows": rows},
        },
        "branches": {"east": rows},
        "policy": policy,
        "final_revision": 0,
        "required_packets": ["case", "policy.audit", "ledger.east"],
        "expected": {"totals": {"east": 100}, "flagged": []},
    }
    world = DecisionWorld(task, tmp_path)
    record = world.audit_selection({"east": ["old"]}, [], 0)
    answer = world.stage_audit([record["record_id"]])
    grade = score_fair(task, answer, world.export())
    assert grade["feasible"] is True
    assert grade["passed"] is False
    assert "incorrect_selected_event_ids" in grade["failures"]
