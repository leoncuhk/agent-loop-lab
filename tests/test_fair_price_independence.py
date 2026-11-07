import random
from itertools import product

from loop_lab.fair.world import DecisionWorld
from loop_lab.hard.oracles import evaluate_plan
from loop_lab.hard.tasks import plan_task


def test_public_quote_service_matches_independent_oracle_on_development_combinations(tmp_path):
    for size in [3, 4, 5]:
        task = plan_task(f"dev-{size}", 211, group_count=size, delivery="quote")
        world = DecisionWorld(task, tmp_path / str(size))
        case = task["packets"]["case"]
        groups = [[p["id"] for p in task["products"] if p["group"] == g] for g in case["groups"]]
        candidates = list(product(*groups))
        if size > 3:
            candidates = random.Random(20261107).sample(candidates, 96)
        for i in range(0, len(candidates), 16):
            batch = [list(ids) for ids in candidates[i : i + 16]]
            observed = world.assess_candidates(batch, case["requirements"], 0)["new_quotes"]
            assert len(observed) == len(batch)
            for ids, quote in zip(batch, observed, strict=True):
                expected = evaluate_plan(case, task["policy"], task["products"], ids)
                assert quote["cash_cents"] == expected["cash_cents"]
                assert quote["utility"] == expected["utility"]
                assert quote["feasible"] == expected["feasible"]
