"""Outcome checks plus explicit goal and selected-event integrity."""

from loop_lab.fair.world import Requirements
from loop_lab.hard.scoring import score_hard


def score_fair(task, answer, world, error=None):
    result = score_hard(task, answer, world, error)
    final = [p for p in world["proposals"] if p["turn"] == task["final_revision"]]
    if not final:
        return result
    proposal = final[0]
    if task["family"] != "audit":
        wanted = Requirements.model_validate(task["latest_case"]["requirements"]).model_dump(
            mode="json"
        )
        wanted["excluded"] = sorted(set(wanted["excluded"]))
        result["goal_consistent"] = proposal.get("goal_snapshot") == wanted
        if not result["goal_consistent"]:
            result["failures"].append("goal_snapshot_mismatch")
    else:
        selected = {}
        for record_id in proposal.get("audit_record_ids", []):
            record = world["records"][record_id]
            selected.update(record["selected_events"])
        wanted = {}
        for branch, rows in task["branches"].items():
            within = [
                r for r in rows if r["kind"] == "posting" and r["at"] <= task["policy"]["cutoff"]
            ]
            latest = {}
            for row in sorted(within, key=lambda r: r["seq"]):
                latest[row["ref"]] = row
            wanted[branch] = sorted(r["id"] for r in latest.values() if r["state"] == "approved")
        result["event_selection_correct"] = selected == wanted
        if not result["event_selection_correct"]:
            result["failures"].append("incorrect_selected_event_ids")
    result["failures"] = sorted(set(result["failures"]))
    result["passed"] = not result["failures"]
    return result
