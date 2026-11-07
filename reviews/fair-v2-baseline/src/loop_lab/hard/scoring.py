"""Separate feasibility, optimality and complete evidence-backed delivery."""

from loop_lab.hard.oracles import evaluate_plan
from loop_lab.hard.world import render_fact_value
from loop_lab.schema import Answer


def score_hard(task, answer, world, error=None):
    failures = []
    if error:
        failures.append("runtime_error")
    result = {
        "feasible": False,
        "optimal": False,
        "utility_gap": None,
        "audit_branch_accuracy": None,
        "failures": failures,
        "passed": False,
    }
    try:
        answer = Answer.model_validate(answer).model_dump()
    except Exception:
        failures.append("invalid_answer")
        answer = {"status": None, "facts": [], "receipt_ids": []}
    if answer["status"] != "completed":
        failures.append("not_completed")
    reads = {
        e["args"]["packet_id"]
        for e in world["events"]
        if e["tool"] == "read_packet" and "data" in e["result"]
    }
    if set(task["required_packets"]) - reads:
        failures.append("missing_required_evidence")
    submissions = [e for e in world["events"] if e["tool"] == "stage_result"]
    if any(e["turn"] < task["final_revision"] for e in submissions):
        failures.append("premature_submission")
    if any(e["result"].get("error") == "stale_revision" for e in submissions):
        failures.append("stale_revision_attempt")
    final = [p for p in world["proposals"] if p["turn"] == task["final_revision"]]
    if len(final) != 1:
        failures.append("missing_final_draft")
        return result
    proposal = final[0]
    if set(task["required_packets"]) - set(proposal.get("seen_packets", [])):
        failures.append("evidence_after_submission")
    payload = proposal["payload"]
    if answer["receipt_ids"] != [proposal["receipt_id"]]:
        failures.append("receipt_mismatch")
    expected_facts = {k: render_fact_value(v) for k, v in payload.items()}
    received = {}
    for claim in answer["facts"]:
        if (
            claim["source"] != proposal["receipt_id"]
            or expected_facts.get(claim["key"]) != claim["value"]
        ):
            failures.append("unsupported_final_fact")
        received[claim["key"]] = claim["value"]
    if received != expected_facts:
        failures.append("incomplete_final_facts")
    if payload.get("policy_version") != task["policy"]["version"]:
        failures.append("wrong_policy_version")
    if task["family"] == "audit":
        expected = task["expected"]
        totals = payload.get("totals", {})
        correct = sum(totals.get(k) == v for k, v in expected["totals"].items())
        result["audit_branch_accuracy"] = correct / len(expected["totals"])
        if totals != expected["totals"]:
            failures.append("incorrect_branch_totals")
        if payload.get("flagged") != expected["flagged"]:
            failures.append("incorrect_flagged_refs")
        result["feasible"] = (
            totals == expected["totals"] and payload.get("flagged") == expected["flagged"]
        )
        result["optimal"] = result["feasible"]
    else:
        value = evaluate_plan(
            task["latest_case"], task["policy"], task["products"], payload.get("selected_ids", [])
        )
        result["feasible"] = value["feasible"]
        if not value["feasible"]:
            failures.extend(value["errors"])
        if value.get("cash_cents") != payload.get("cash_cents"):
            failures.append("misreported_cash")
        if value.get("utility") != payload.get("utility"):
            failures.append("misreported_utility")
        best = task["expected"]
        if value["feasible"]:
            result["utility_gap"] = best["utility"] - value["utility"]
            result["optimal"] = value["selected_ids"] == best["selected_ids"]
        if not result["optimal"]:
            failures.append("not_optimal")
    result["failures"] = sorted(set(failures))
    result["passed"] = not failures
    return result
