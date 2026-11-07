from loop_lab.domain import BusinessWorld


def test_return_needs_approval_and_is_idempotent_after_approval(tmp_path):
    world = BusinessWorld(
        tmp_path / "world.sqlite",
        user_id="alice",
        orders={
            "O1": {
                "owner": "alice",
                "status": "delivered",
                "age_days": 5,
                "amount_cents": 8900,
                "tracking_id": "S1",
            }
        },
        shipments={},
    )
    denied = world.create_return("O1", approved=False)
    assert denied["error"] == "approval_required"
    assert world.receipts() == []
    first = world.create_return("O1", approved=True)
    repeat = world.create_return("O1", approved=True)
    assert first["receipt_id"] == repeat["receipt_id"]
    assert world.receipts() == [
        {"receipt_id": first["receipt_id"], "order_id": "O1", "amount_cents": 8900}
    ]
