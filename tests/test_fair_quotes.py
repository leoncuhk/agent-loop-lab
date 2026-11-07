from copy import deepcopy

from loop_lab.fair.world import DecisionWorld
from loop_lab.hard.tasks import plan_task


def test_quote_binds_products_money_and_revision(tmp_path):
    task = plan_task("dev", 211)
    world = DecisionWorld(task, tmp_path)
    goal = task["packets"]["case"]["requirements"]
    assessed = world.assess_candidates([["B1", "C5", "T5"], ["B2", "C6", "T2"]], goal, 0)
    assert assessed["best"]["selected_ids"] == ["B2", "C6", "T2"]
    assert assessed["best"]["cash_cents"] == 9958
    quote_id = assessed["best"]["quote_id"]
    result = world.stage_quote(quote_id)
    assert result["status"] == "completed"
    assert world.proposals[0]["payload"]["cash_cents"] == 9958
    assert world.proposals[0]["payload"]["selected_ids"] == ["B2", "C6", "T2"]
    assert world.stage_quote(quote_id) == result
    world.turn = 1
    assert world.stage_quote(quote_id)["error"] == "stale_revision"


def test_private_gold_cannot_change_quotes(tmp_path):
    task = plan_task("dev", 211)
    poisoned = deepcopy(task)
    poisoned["expected"] = {"sentinel": "DO_NOT_EXPOSE_GOLD"}
    poisoned["latest_case"]["requirements"]["budget"] = -12345
    a = DecisionWorld(task, tmp_path / "a")
    b = DecisionWorld(poisoned, tmp_path / "b")
    goal = task["packets"]["case"]["requirements"]
    ra = a.assess_candidates([["B2", "C6", "T2"]], goal, 0)
    rb = b.assess_candidates([["B2", "C6", "T2"]], goal, 0)
    assert ra == rb
    assert "expected" not in b.task and "latest_case" not in b.task and "messages" not in b.task


def test_assessment_does_not_search_unrequested_candidates(tmp_path):
    task = plan_task("dev", 211)
    world = DecisionWorld(task, tmp_path)
    result = world.assess_candidates(
        [["B1", "C5", "T5"]], task["packets"]["case"]["requirements"], 0
    )
    assert result["best"]["selected_ids"] == ["B1", "C5", "T5"]
    assert result["evaluated_count"] == 1
    assert result["global_optimality"] == "not_established"


def test_quote_expires_when_public_policy_changes(tmp_path):
    task = plan_task("dev", 211)
    world = DecisionWorld(task, tmp_path)
    goal = task["packets"]["case"]["requirements"]
    quote = world.assess_candidates([["B2", "C6", "T2"]], goal, 0)["best"]
    updated = {**task["packets"]["policy.current"], "credit_bps": 0}
    world.update_source("policy.current", updated)
    assert world.stage_quote(quote["quote_id"])["error"] == "stale_business_data"
    assert world.proposals == []


def test_funds_update_cannot_overwrite_an_already_observed_quote(tmp_path):
    task = plan_task("dev", 211)
    world = DecisionWorld(task, tmp_path)
    goal = {**task["packets"]["case"]["requirements"], "budget": 100000}
    old = world.assess_candidates([["B2", "C6", "T2"]], goal, 0)["best"]
    case = deepcopy(task["packets"]["case"])
    case["funds"]["posted_credit"] = 0
    world.update_source("case", case)
    new = world.assess_candidates([["B2", "C6", "T2"]], goal, 0)["best"]
    assert old["quote_id"] != new["quote_id"]
    assert old["cash_cents"] == world.export()["records"][old["quote_id"]]["cash_cents"]
    assert world.stage_quote(old["quote_id"], {old["quote_id"]})["error"] == "stale_business_data"
    assert world.stage_quote(new["quote_id"], {old["quote_id"]})["error"] == "quote_not_observed"
