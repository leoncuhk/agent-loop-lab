from loop_lab.hard.oracles import audit_truth, evaluate_plan, optimal_plan


def test_plan_discount_shipping_credit_and_compatibility():
    case = {
        "groups": ["base", "battery"],
        "requirements": {
            "budget": 9000,
            "deadline": "2026-11-18",
            "excluded": [],
            "platform": "any",
        },
        "funds": {"posted_credit": 2000, "pending_credit": 9000},
    }
    policy = {
        "version": "v2",
        "discount_threshold": 10000,
        "discount_bps": 1000,
        "free_shipping_threshold": 9500,
        "shipping": {"W1": 500},
        "credit_bps": 2000,
    }
    products = [
        {
            "id": "B",
            "group": "base",
            "price": 6000,
            "utility": 40,
            "warehouse": "W1",
            "platform": "A",
            "eta": "2026-11-17",
            "stock": 1,
        },
        {
            "id": "P",
            "group": "battery",
            "price": 4000,
            "utility": 30,
            "warehouse": "W1",
            "platform": "A",
            "eta": "2026-11-17",
            "stock": 1,
        },
    ]
    result = evaluate_plan(case, policy, products, ["B", "P"])
    assert result["cash_cents"] == 7700  # 10000-1000 +500 -1800, pending credit unavailable
    assert result["utility"] == 70 and result["feasible"]
    assert optimal_plan(case, policy, products)["selected_ids"] == ["B", "P"]


def test_audit_uses_snapshot_before_latest_revision_and_deduplicates():
    rows = [
        {
            "kind": "posting",
            "id": "a1",
            "ref": "A",
            "seq": 1,
            "at": 10,
            "state": "approved",
            "amount": 1000,
        },
        {
            "kind": "posting",
            "id": "a2",
            "ref": "A",
            "seq": 2,
            "at": 30,
            "state": "void",
            "amount": 0,
        },
        {
            "kind": "posting",
            "id": "b1",
            "ref": "B",
            "seq": 1,
            "at": 12,
            "state": "pending",
            "amount": 2100,
        },
    ]
    rows.append(dict(rows[0]))
    assert audit_truth({"east": rows}, cutoff=20, flag_threshold=1500) == {
        "totals": {"east": 1000},
        "flagged": ["east/B"],
    }
